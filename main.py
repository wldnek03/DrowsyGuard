# main.py
# DrowsyGuard - 졸음 감지 경보 시스템 (세션 + 상태머신)
# 흐름: IDLE → ACTIVE → (DANGER 시 3초 응시 해제) → STRETCHING → ACTIVE → SUMMARY → IDLE
# ─────────────────────────────────────────────────
# 핀 배치
#   I2C SDA     : GPIO2  (Pin 3)   — OLED + BMP180
#   I2C SCL     : GPIO3  (Pin 5)   — OLED + BMP180
#   버튼        : GPIO17 (Pin 11)  — 세션 시작/종료
#   부저        : GPIO18 (Pin 12)
#   조이스틱 SW : GPIO27 (Pin 13)  — 화면 전환
#   LED 초록    : GPIO22 (Pin 15)  — NORMAL
#   LED 노랑    : GPIO23 (Pin 16)  — CAUTION
#   LED 빨강    : GPIO24 (Pin 18)  — DANGER
#   초음파 TRIG : GPIO5  (Pin 29)
#   초음파 ECHO : GPIO6  (Pin 31)  ※ 분압 회로 필수
# ─────────────────────────────────────────────────

import cv2
import csv
import os
import time
import threading
import collections
import smbus2
import numpy as np
from datetime import datetime

import RPi.GPIO as GPIO
from picamera2          import Picamera2
from gpiozero           import LED, Button, PWMOutputDevice
from luma.core.interface.serial import i2c
from luma.oled.device   import ssd1306
from luma.core.render   import canvas
from PIL                import ImageFont
import dlib
from imutils            import face_utils


# ═══════════════════════════════════════════════════
# 1. 상수 / 핀 설정
# ═══════════════════════════════════════════════════

CAUTION_THRESHOLD = 40
DANGER_THRESHOLD  = 70

EAR_THRESHOLD       = 0.25
FRAME_THRESHOLD     = 30
GAZE_RELEASE_FRAMES = 40   # 약 2초

# 하품 (MAR: Mouth Aspect Ratio)
MAR_THRESHOLD = 0.30       # 이 값 이상이면 입 벌린 것 (평소 0.05~0.15, 하품 0.30+)
YAWN_FRAMES   = 5          # 0.25초 유지하면 하품 1회로 카운트

TEMP_HOT  = 27.0
PRES_LOW  = 1005.0

TRIG_PIN        = 5
ECHO_PIN        = 6
CLOSE_THRESHOLD = 25.0
FAR_THRESHOLD   = 80.0
SURGE_THRESHOLD = 15.0

BUZZER_PIN  = 18
BUTTON_PIN  = 17
JOY_SW_PIN  = 27
LED_GREEN   = 22
LED_YELLOW  = 23
LED_RED     = 24

BMP180_ADDR = 0x77
OLED_PORT   = 1
OLED_ADDR   = 0x3C
OLED_W, OLED_H = 128, 64

LOG_PATH = os.path.expanduser('~/drowsy_log.csv')


# ═══════════════════════════════════════════════════
# 2. 하드웨어 초기화
# ═══════════════════════════════════════════════════

led_green  = LED(LED_GREEN)
led_yellow = LED(LED_YELLOW)
led_red    = LED(LED_RED)

# Passive 부저(3핀): PWM 신호로 주파수 제어 (옛날 ToneBuzzer 방식과 동일)
try:
    buzzer = PWMOutputDevice(BUZZER_PIN)
    buzzer.value = 0
except Exception as e:
    print(f'[경고] 부저 초기화 실패: {e}')
    buzzer = None

button  = Button(BUTTON_PIN, pull_up=True, bounce_time=0.05)
joy_btn = Button(JOY_SW_PIN, pull_up=True, bounce_time=0.05)

print(f'[init] 버튼 초기 상태: is_pressed={button.is_pressed} (눌렀을 때 True여야 함)')

GPIO.setmode(GPIO.BCM)
GPIO.setwarnings(False)
GPIO.setup(TRIG_PIN, GPIO.OUT)
GPIO.setup(ECHO_PIN, GPIO.IN)

_serial = i2c(port=OLED_PORT, address=OLED_ADDR)
device  = ssd1306(_serial, width=OLED_W, height=OLED_H)

try:
    FONT_SM = ImageFont.truetype('/usr/share/fonts/truetype/nanum/NanumGothic.ttf', 10)
    FONT_MD = ImageFont.truetype('/usr/share/fonts/truetype/nanum/NanumGothic.ttf', 13)
    FONT_LG = ImageFont.truetype('/usr/share/fonts/truetype/nanum/NanumGothic.ttf', 18)
    print('[init] NanumGothic 한글 폰트 로드 완료')
except Exception as e:
    print(f'[경고] 한글 폰트 로드 실패: {e} — 한글이 깨질 수 있음')
    FONT_SM = ImageFont.load_default()
    FONT_MD = FONT_SM
    FONT_LG = FONT_SM

_detector  = dlib.get_frontal_face_detector()
_predictor = dlib.shape_predictor('shape_predictor_68_face_landmarks.dat')
(lStart, lEnd) = face_utils.FACIAL_LANDMARKS_IDXS['left_eye']
(rStart, rEnd) = face_utils.FACIAL_LANDMARKS_IDXS['right_eye']
(mStart, mEnd) = face_utils.FACIAL_LANDMARKS_IDXS['mouth']      # 48~68


# ═══════════════════════════════════════════════════
# 3. LED / 부저
# ═══════════════════════════════════════════════════

def set_led(status, env_warning=False, pending_release=False):
    """LED 점등 규칙:
       - DANGER 또는 응시 해제 진행 중: 빨강 (부저와 함께 유지)
       - CAUTION: 노랑
       - NORMAL: 초록 (+환경 경고 있으면 노랑도 함께)
    """
    led_green.off(); led_yellow.off(); led_red.off()
    if status == 'DANGER' or pending_release:
        led_red.on()
    elif status == 'CAUTION':
        led_yellow.on()
    else:  # NORMAL
        led_green.on()
        if env_warning:
            led_yellow.on()   # 초록+노랑 동시 점등 (환경 경고 표시)

def all_leds_off():
    led_green.off(); led_yellow.off(); led_red.off()

def _play_freq(freq, duration):
    """PWM으로 특정 주파수 소리 출력 (옛날 buzzer.play(freq)와 동일)"""
    if buzzer is None:
        return
    buzzer.frequency = freq
    buzzer.value = 0.5      # duty cycle 50% = 최대 음량
    time.sleep(duration)
    buzzer.value = 0

def beep_once():
    """주의(CAUTION) 단계 짧은 경고음 - 옛날 코드와 동일"""
    _play_freq(440, 0.3)    # A4
    time.sleep(0.1)

def beep_alarm_normal():
    """위험(DANGER) 단계 연속 경보 - 옛날 코드와 동일"""
    if buzzer is None:
        return
    for _ in range(3):
        _play_freq(880, 0.2)  # A5 (높은 음)
        time.sleep(0.1)

def beep_alarm_hot():
    """더운 환경 경보 - 낮은 음으로 차별화"""
    if buzzer is None:
        return
    for _ in range(2):
        _play_freq(330, 0.35) # E4 (낮은 음)
        time.sleep(0.15)

def stop_buzzer():
    if buzzer is not None:
        buzzer.value = 0


# ═══════════════════════════════════════════════════
# 4. 조이스틱 (화면 전환)
# ═══════════════════════════════════════════════════

SCREEN_MODES = ['risk', 'env', 'posture']
_screen_idx  = [0]

def _on_joy_click():
    _screen_idx[0] = (_screen_idx[0] + 1) % len(SCREEN_MODES)
    print(f'[조이스틱] 화면 전환 → {SCREEN_MODES[_screen_idx[0]]}')

joy_btn.when_pressed = _on_joy_click

def get_screen():
    return SCREEN_MODES[_screen_idx[0]]


# ═══════════════════════════════════════════════════
# 5. 버튼 이벤트 (시작/종료 토글)
# ═══════════════════════════════════════════════════

_button_event = threading.Event()

def _on_btn_pressed():
    _button_event.set()

button.when_pressed = _on_btn_pressed


# ═══════════════════════════════════════════════════
# 6. EAR
# ═══════════════════════════════════════════════════

def _calc_ear(eye):
    A = np.linalg.norm(eye[1] - eye[5])
    B = np.linalg.norm(eye[2] - eye[4])
    C = np.linalg.norm(eye[0] - eye[3])
    return (A + B) / (2.0 * C) if C != 0 else 0

def _calc_mar(mouth):
    """MAR (Mouth Aspect Ratio): 입 벌림 비율
       mouth = shape[48:68] (20개 점)
       내부 입술 위/아래 거리 / 입꼬리 좌/우 거리
       평상시 ≈ 0.1, 하품 ≈ 0.5~0.8
    """
    # mouth 인덱스 (mouth[0]이 48번 landmark)
    # 62번(상단 안쪽 중앙) = mouth[14], 66번(하단 안쪽 중앙) = mouth[18]
    # 60번(왼 입꼬리 안쪽) = mouth[12], 64번(오른 입꼬리 안쪽) = mouth[16]
    A = np.linalg.norm(mouth[14] - mouth[18])
    B = np.linalg.norm(mouth[13] - mouth[19])
    C = np.linalg.norm(mouth[12] - mouth[16])
    return (A + B) / (2.0 * C) if C != 0 else 0

def get_face_features(frame):
    """프레임에서 EAR, MAR, 얼굴 감지 여부 한꺼번에 추출"""
    gray  = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    faces = _detector(gray, 0)
    if len(faces) == 0:
        return None, None, False
    shape     = face_utils.shape_to_np(_predictor(gray, faces[0]))
    left_eye  = shape[lStart:lEnd]
    right_eye = shape[rStart:rEnd]
    mouth     = shape[mStart:mEnd]
    ear = round((_calc_ear(left_eye) + _calc_ear(right_eye)) / 2.0, 4)
    mar = round(_calc_mar(mouth), 4)
    return ear, mar, True


# ═══════════════════════════════════════════════════
# 7. BMP180
# ═══════════════════════════════════════════════════

class BMP180:
    def __init__(self):
        self.bus = smbus2.SMBus(1)
        self._load_calibration()

    def _read_s16(self, reg):
        msb, lsb = self.bus.read_i2c_block_data(BMP180_ADDR, reg, 2)
        v = (msb << 8) + lsb
        return v - 65536 if v > 32767 else v

    def _read_u16(self, reg):
        msb, lsb = self.bus.read_i2c_block_data(BMP180_ADDR, reg, 2)
        return (msb << 8) + lsb

    def _load_calibration(self):
        self.AC1 = self._read_s16(0xAA); self.AC2 = self._read_s16(0xAC)
        self.AC3 = self._read_s16(0xAE); self.AC4 = self._read_u16(0xB0)
        self.AC5 = self._read_u16(0xB2); self.AC6 = self._read_u16(0xB4)
        self.B1  = self._read_s16(0xB6); self.B2  = self._read_s16(0xB8)
        self.MB  = self._read_s16(0xBA); self.MC  = self._read_s16(0xBC)
        self.MD  = self._read_s16(0xBE)

    def _raw_temp(self):
        self.bus.write_byte_data(BMP180_ADDR, 0xF4, 0x2E)
        time.sleep(0.005)
        msb, lsb = self.bus.read_i2c_block_data(BMP180_ADDR, 0xF6, 2)
        return (msb << 8) + lsb

    def _raw_pressure(self, oss=0):
        self.bus.write_byte_data(BMP180_ADDR, 0xF4, 0x34 + (oss << 6))
        time.sleep(0.005)
        msb, lsb, xlsb = self.bus.read_i2c_block_data(BMP180_ADDR, 0xF6, 3)
        return ((msb << 16) + (lsb << 8) + xlsb) >> (8 - oss)

    def read_pressure(self):
        oss = 0
        UT  = self._raw_temp()
        X1  = ((UT - self.AC6) * self.AC5) >> 15
        X2  = (self.MC << 11) // (X1 + self.MD)
        B5  = X1 + X2
        temp = ((B5 + 8) >> 4) / 10.0
        UP = self._raw_pressure(oss)
        B6 = B5 - 4000
        X1 = (self.B2 * ((B6 * B6) >> 12)) >> 11
        X2 = (self.AC2 * B6) >> 11
        B3 = (((self.AC1 * 4 + X1 + X2) << oss) + 2) >> 2
        X1 = (self.AC3 * B6) >> 13
        X2 = (self.B1 * ((B6 * B6) >> 12)) >> 16
        X3 = ((X1 + X2) + 2) >> 2
        B4 = (self.AC4 * (X3 + 32768)) >> 15
        B7 = (UP - B3) * (50000 >> oss)
        p  = (B7 * 2) // B4 if B7 < 0x80000000 else (B7 // B4) * 2
        X1 = (p >> 8) * (p >> 8)
        X1 = (X1 * 3038) >> 16
        X2 = (-7357 * p) >> 16
        p  = p + ((X1 + X2 + 3791) >> 4)
        return round(temp, 1), round(p / 100.0, 1)


# ═══════════════════════════════════════════════════
# 8. 초음파 + 자세
# ═══════════════════════════════════════════════════

def _measure_once():
    GPIO.output(TRIG_PIN, False)
    time.sleep(0.002)
    GPIO.output(TRIG_PIN, True)
    time.sleep(0.00001)
    GPIO.output(TRIG_PIN, False)
    timeout = time.time() + 0.04
    while GPIO.input(ECHO_PIN) == 0:
        if time.time() > timeout:
            return None
    pulse_start = time.time()
    timeout = time.time() + 0.04
    while GPIO.input(ECHO_PIN) == 1:
        if time.time() > timeout:
            return None
    pulse_end = time.time()
    dist = (pulse_end - pulse_start) * 17150
    return round(dist, 1) if 2 <= dist <= 400 else None

def get_distance_cm():
    samples = [_measure_once() for _ in range(3)]
    valid   = sorted(v for v in samples if v is not None)
    return valid[len(valid) // 2] if valid else None


class PostureAnalyzer:
    def __init__(self, window=5):
        self._buf = collections.deque(maxlen=window)

    def update(self, dist_cm):
        if dist_cm is not None:
            self._buf.append(dist_cm)

    def analyze(self, dist_cm):
        too_close = (dist_cm is not None and dist_cm < CLOSE_THRESHOLD)
        desk_away = (dist_cm is not None and dist_cm > FAR_THRESHOLD)
        unstable  = False
        if len(self._buf) >= 2:
            diffs = [abs(self._buf[i] - self._buf[i-1])
                     for i in range(1, len(self._buf))]
            if max(diffs) >= SURGE_THRESHOLD:
                unstable = True
        score = 0
        if too_close: score += 15
        if desk_away: score += 10
        if unstable:  score += 5
        return {'too_close': too_close, 'desk_away': desk_away,
                'unstable': unstable,   'posture_score': score}


# ═══════════════════════════════════════════════════
# 9. OLED 화면 (한국어)
# ═══════════════════════════════════════════════════

def _progress_bar(draw, x, y, w, h, pct):
    draw.rectangle([x, y, x + w, y + h], outline='white', fill='black')
    bar_w = int((w - 2) * pct / 100)
    if bar_w > 0:
        draw.rectangle([x + 1, y + 1, x + 1 + bar_w, y + h - 1], fill='white')

def _status_icon(draw, x, y, status):
    icons = {'NORMAL': '[정상]', 'CAUTION': '[주의]', 'DANGER': '[위험]'}
    draw.text((x, y), icons.get(status, '?'), font=FONT_SM, fill='white')

def show_idle():
    with canvas(device) as draw:
        draw.text((16,  2), 'DrowsyGuard',     font=FONT_MD, fill='white')
        draw.line([(0, 20), (OLED_W, 20)],     fill='white', width=1)
        draw.text(( 8, 26), '버튼을 눌러',      font=FONT_SM, fill='white')
        draw.text(( 8, 38), '공부를 시작하세요', font=FONT_SM, fill='white')
        draw.text((36, 52), '[ 대기중 ]',       font=FONT_SM, fill='white')

def show_dashboard(risk, status, eye_ratio, posture, elapsed_min):
    label = {'NORMAL': '정상', 'CAUTION': '주의', 'DANGER': '위험!'}[status]
    with canvas(device) as draw:
        draw.text((0, 0),  f'공부 {elapsed_min:>3}분',  font=FONT_SM, fill='white')
        _status_icon(draw, 78, 0, status)
        draw.text((0, 13), f'{risk}%',                  font=FONT_LG, fill='white')
        draw.text((52, 18), label,                      font=FONT_SM, fill='white')
        _progress_bar(draw, 0, 34, OLED_W - 1, 8, risk)
        eye_pct  = int((eye_ratio or 0) * 100)
        draw.text((0, 46), f'눈:{eye_pct}%',            font=FONT_SM, fill='white')
        if posture:
            flags    = (['숙임'] if posture['too_close'] else []) + \
                       (['이탈'] if posture['desk_away'] else []) + \
                       (['흔들'] if posture['unstable']  else [])
            pose_str = ' '.join(flags) if flags else '양호'
        else:
            pose_str = '--'
        draw.text((56, 46), f'자세:{pose_str}',         font=FONT_SM, fill='white')

def show_environment(temp, pressure):
    if   temp > TEMP_HOT and pressure < PRES_LOW: advice = '환기 강력 권장!'
    elif temp > TEMP_HOT:                         advice = '온도 높음 - 환기'
    elif pressure < PRES_LOW:                     advice = '저기압 - 환기'
    else:                                         advice = '환경 양호'
    with canvas(device) as draw:
        draw.text((0,  0), '[ 환경 정보 ]',              font=FONT_SM, fill='white')
        draw.line([(0, 12), (OLED_W, 12)],               fill='white', width=1)
        draw.text((0, 15), f'온도   {temp:.1f} C',       font=FONT_MD, fill='white')
        draw.text((0, 32), f'기압   {pressure:.0f} hPa', font=FONT_MD, fill='white')
        draw.line([(0, 50), (OLED_W, 50)],               fill='white', width=1)
        draw.text((0, 52), advice,                       font=FONT_SM, fill='white')

def show_posture_eye(ear, frame_count, distance_cm, posture):
    ear_str  = f'{ear:.2f}' if ear is not None else '--'
    dist_str = f'{distance_cm:.0f}cm' if distance_cm is not None else '--'
    eye_pct  = min(100, int(frame_count / FRAME_THRESHOLD * 100))
    if posture:
        if   posture['desk_away']:  pose_msg = '책상 이탈'
        elif posture['too_close']:  pose_msg = '고개 숙임'
        elif posture['unstable']:   pose_msg = '자세 불안정'
        else:                       pose_msg = '자세 양호'
    else:
        pose_msg = '센서 없음'
    with canvas(device) as draw:
        draw.text((0, 0), '[ 자세 & 눈 ]',           font=FONT_SM, fill='white')
        draw.line([(0, 12), (OLED_W, 12)],          fill='white', width=1)
        draw.text((0, 15), f'EAR {ear_str}',        font=FONT_SM, fill='white')
        _progress_bar(draw, 0, 26, 60, 6, eye_pct)
        draw.text((64, 22), '눈감김',                font=FONT_SM, fill='white')
        draw.text((0, 36), f'거리 {dist_str}',       font=FONT_SM, fill='white')
        draw.text((0, 48), pose_msg,                 font=FONT_SM, fill='white')

def show_release(release_frames, face_detected):
    pct = int(release_frames / GAZE_RELEASE_FRAMES * 100)
    pct = max(0, min(100, pct))
    with canvas(device) as draw:
        draw.rectangle(device.bounding_box, outline='white', fill='black')
        draw.text((20,  3), '!! 졸음 경보 !!',         font=FONT_SM, fill='white')
        draw.line([(0, 16), (OLED_W, 16)],            fill='white', width=1)
        msg = '카메라 응시!' if face_detected else '얼굴이 안보여요'
        draw.text((10, 20), msg,                      font=FONT_SM, fill='white')
        draw.text((28, 32), '2초간 유지',              font=FONT_SM, fill='white')
        _progress_bar(draw, 8, 46, OLED_W - 17, 10, pct)

def show_stretching_step(en_label, ko_label, sec_left, step_idx, total):
    with canvas(device) as draw:
        draw.text((0,  0), f'스트레칭 {step_idx}/{total}', font=FONT_SM, fill='white')
        draw.line([(0, 12), (OLED_W, 12)],                 fill='white', width=1)
        draw.text((4, 16), ko_label,                       font=FONT_MD, fill='white')
        draw.text((4, 34), en_label,                       font=FONT_SM, fill='white')
        draw.text((30, 48), f'{sec_left:>2} 초',           font=FONT_MD, fill='white')

def show_summary(session):
    dur_min   = (session['end'] - session['start']) / 60.0
    danger_m  = session['danger_total_sec'] / 60.0
    with canvas(device) as draw:
        draw.text((16, 0), '[ 세션 요약 ]',                                font=FONT_SM, fill='white')
        draw.line([(0, 12), (OLED_W, 12)],                                 fill='white', width=1)
        draw.text((0, 14), f'시간 {dur_min:5.1f}분',                       font=FONT_SM, fill='white')
        draw.text((0, 25), f'위험 {session["danger_count"]:>2}회 ({danger_m:.1f}분)', font=FONT_SM, fill='white')
        draw.text((0, 36), f'주의 {session["caution_count"]:>2}회',        font=FONT_SM, fill='white')
        draw.text((0, 47), f'하품 {session["yawn_count"]:>2}회',           font=FONT_SM, fill='white')

def show_calibration(collected, target):
    pct = int(collected / target * 100)
    with canvas(device) as draw:
        draw.text((10,  2), '[ 캘리브레이션 ]',     font=FONT_SM, fill='white')
        draw.line([(0, 16), (OLED_W, 16)],          fill='white', width=1)
        draw.text(( 4, 20), '카메라 정면을',         font=FONT_SM, fill='white')
        draw.text(( 4, 32), '3초간 응시하세요',      font=FONT_SM, fill='white')
        _progress_bar(draw, 8, 48, OLED_W - 17, 10, pct)

def show_boot():
    with canvas(device) as draw:
        draw.text((18,  6), 'DrowsyGuard',  font=FONT_MD, fill='white')
        draw.text((28, 24), '세션 모드',      font=FONT_SM, fill='white')
        draw.text((12, 36), '눈+환경+자세',   font=FONT_SM, fill='white')
        draw.text((30, 50), '시작중...',      font=FONT_SM, fill='white')

def oled_clear():
    with canvas(device) as draw:
        draw.rectangle(device.bounding_box, fill='black')


# ═══════════════════════════════════════════════════
# 10. 경보 스레드
# ═══════════════════════════════════════════════════

_alarm_active = False
_alarm_hot    = False
_alarm_lock   = threading.Lock()

def _alarm_thread_fn():
    while True:
        with _alarm_lock:
            active = _alarm_active
            hot    = _alarm_hot
        if active:
            if hot:
                beep_alarm_hot()
            else:
                beep_alarm_normal()
            time.sleep(0.4)
        else:
            time.sleep(0.1)

threading.Thread(target=_alarm_thread_fn, daemon=True).start()

def start_alarm(hot=False):
    global _alarm_active, _alarm_hot
    with _alarm_lock:
        _alarm_active = True
        _alarm_hot    = hot

def stop_alarm():
    global _alarm_active
    with _alarm_lock:
        _alarm_active = False
    stop_buzzer()


# ═══════════════════════════════════════════════════
# 11. 위험도
# ═══════════════════════════════════════════════════

def time_weight():
    """시간대별 졸음 위험 가중치
       - 점심 후 (13~16시): 1.20
       - 새벽 (2~6시): 1.30
       - 늦은 밤 (22시~2시): 1.15
       - 그 외: 1.00
    """
    hour = datetime.now().hour
    if 13 <= hour < 16:                 return 1.20
    if 2  <= hour < 6:                  return 1.30
    if hour >= 22 or hour < 2:          return 1.15
    return 1.00

def calc_risk(frame_cnt, temp, pressure, posture, ear, ear_threshold=EAR_THRESHOLD):
    eye_score = min(70, int(frame_cnt / FRAME_THRESHOLD * 70))
    if posture.get('too_close') and ear is not None and ear < ear_threshold:
        eye_score = min(70, int(eye_score * 1.3))
    env_score  = (15 if temp > TEMP_HOT else 0) + (15 if pressure < PRES_LOW else 0)
    dist_score = min(30, posture.get('posture_score', 0))
    total      = (eye_score + env_score + dist_score) * time_weight()
    return min(100, int(total))

def get_status(risk):
    if risk >= DANGER_THRESHOLD:  return 'DANGER'
    if risk >= CAUTION_THRESHOLD: return 'CAUTION'
    return 'NORMAL'


# ═══════════════════════════════════════════════════
# 12. 센서 백그라운드 루프
# ═══════════════════════════════════════════════════

temp        = 25.0
pressure    = 1013.0
distance_cm = 999.0
posture     = {'too_close': False, 'desk_away': False,
               'unstable': False,  'posture_score': 0}

def _sensor_loop():
    global temp, pressure, distance_cm, posture
    try:
        bmp = BMP180()
    except Exception as e:
        print(f'[경고] BMP180 초기화 실패: {e}')
        bmp = None
    analyzer = PostureAnalyzer(window=5)
    while True:
        if bmp is not None:
            try:
                temp, pressure = bmp.read_pressure()
            except Exception:
                pass
        d = get_distance_cm()
        if d is not None:
            distance_cm = d
        analyzer.update(distance_cm)
        posture = analyzer.analyze(distance_cm)
        time.sleep(2)

threading.Thread(target=_sensor_loop, daemon=True).start()


# ═══════════════════════════════════════════════════
# 13. 세션 + 스트레칭
# ═══════════════════════════════════════════════════

def new_session():
    return {
        'start':            time.time(),
        'end':              None,
        'caution_count':    0,
        'danger_count':     0,
        'danger_total_sec': 0.0,
        'yawn_count':       0,
        '_danger_start':    None,
    }

def session_mark_caution(s):
    s['caution_count'] += 1

def session_mark_danger_start(s):
    s['danger_count'] += 1
    s['_danger_start'] = time.time()

def session_mark_danger_end(s):
    if s['_danger_start'] is not None:
        s['danger_total_sec'] += time.time() - s['_danger_start']
        s['_danger_start'] = None

def save_session_csv(s):
    started = datetime.fromtimestamp(s['start'])
    ended   = datetime.fromtimestamp(s['end'])
    dur_min = (s['end'] - s['start']) / 60.0
    is_new  = not os.path.exists(LOG_PATH)
    with open(LOG_PATH, 'a', newline='') as f:
        w = csv.writer(f)
        if is_new:
            w.writerow(['date', 'start', 'end',
                        'duration_min', 'caution_count',
                        'danger_count', 'danger_min', 'yawn_count'])
        w.writerow([
            started.strftime('%Y-%m-%d'),
            started.strftime('%H:%M:%S'),
            ended.strftime('%H:%M:%S'),
            f'{dur_min:.2f}',
            s['caution_count'],
            s['danger_count'],
            f'{s["danger_total_sec"]/60.0:.2f}',
            s['yawn_count'],
        ])
    print(f'[세션] CSV 저장: {LOG_PATH}')

# 음 주파수: C4=262, D4=294, E4=330, F4=349, G4=392, A4=440, B4=494, C5=523, D5=587

# 베토벤 - 환희의 송가 (Ode to Joy)
ODE_TO_JOY = [
    (330, 0.40), (330, 0.40), (349, 0.40), (392, 0.40),
    (392, 0.40), (349, 0.40), (330, 0.40), (294, 0.40),
    (262, 0.40), (262, 0.40), (294, 0.40), (330, 0.40),
    (330, 0.55), (294, 0.20), (294, 0.60),
    (330, 0.40), (330, 0.40), (349, 0.40), (392, 0.40),
    (392, 0.40), (349, 0.40), (330, 0.40), (294, 0.40),
    (262, 0.40), (262, 0.40), (294, 0.40), (330, 0.40),
    (294, 0.55), (262, 0.20), (262, 0.60),
]

# 모차르트 변주곡 - 작은 별 (Twinkle Twinkle Little Star)
TWINKLE = [
    (262, 0.40), (262, 0.40), (392, 0.40), (392, 0.40),
    (440, 0.40), (440, 0.40), (392, 0.80),
    (349, 0.40), (349, 0.40), (330, 0.40), (330, 0.40),
    (294, 0.40), (294, 0.40), (262, 0.80),
    (392, 0.40), (392, 0.40), (349, 0.40), (349, 0.40),
    (330, 0.40), (330, 0.40), (294, 0.80),
    (392, 0.40), (392, 0.40), (349, 0.40), (349, 0.40),
    (330, 0.40), (330, 0.40), (294, 0.80),
]

# 사운드 오브 뮤직 - 에델바이스 (Edelweiss)
EDELWEISS = [
    (262, 0.50), (330, 0.50), (392, 0.50),
    (262, 0.50), (330, 0.50), (392, 0.50),
    (349, 0.50), (294, 0.50), (494, 0.50),
    (392, 0.80), (262, 0.40), (330, 0.40),
    (262, 0.50), (330, 0.50), (392, 0.50),
    (262, 0.50), (330, 0.50), (392, 0.50),
    (349, 0.50), (294, 0.50), (494, 0.50),
    (262, 1.00),
]


def _play_melody_until(melody, end_ts, en_label, ko_label, step_idx, total):
    """end_ts까지 멜로디를 반복 재생하면서 OLED 카운트다운 표시"""
    note_idx = 0
    last_oled_update = 0.0
    while time.time() < end_ts:
        note, dur = melody[note_idx]
        note_idx = (note_idx + 1) % len(melody)
        if buzzer is not None:
            buzzer.frequency = note
            buzzer.value = 0.5
        note_end = time.time() + dur
        while time.time() < note_end and time.time() < end_ts:
            now = time.time()
            if now - last_oled_update >= 0.25:
                remaining = int(end_ts - now) + 1
                show_stretching_step(en_label, ko_label, remaining, step_idx, total)
                last_oled_update = now
            time.sleep(0.05)
        if buzzer is not None:
            buzzer.value = 0
        time.sleep(0.03)


def run_stretching_routine():
    # (영문, 한글, 시간(초), 멜로디)
    steps = [
        ('Neck rolls',  '목 돌리기',    10, ODE_TO_JOY),  # 환희의 송가
        ('Stretch up',  '기지개 켜기',  10, TWINKLE),     # 작은 별
        ('Drink water', '물 한 모금',   10, EDELWEISS),   # 에델바이스
    ]
    total = len(steps)

    for idx, (en, ko, sec, melody) in enumerate(steps, start=1):
        print(f'[스트레칭 {idx}/{total}] {ko}')
        end_ts = time.time() + sec
        _play_melody_until(melody, end_ts, en, ko, idx, total)

    # 완료 팡파레: 도-미-솔
    if buzzer is not None:
        for note in [523, 659, 784]:
            _play_freq(note, 0.18)


# ═══════════════════════════════════════════════════
# 14. 메인 루프
# ═══════════════════════════════════════════════════

def main():
    global temp, pressure, distance_cm, posture

    try:
        picam2 = Picamera2()
        config = picam2.create_preview_configuration(main={"size": (640, 480)})
        picam2.configure(config)
        picam2.start()
        time.sleep(2)
    except Exception as e:
        print(f'[오류] 카메라 초기화 실패: {e}')
        return

    show_boot()
    time.sleep(2)
    all_leds_off()
    show_idle()

    session_state     = 'IDLE'
    session           = None
    frame_count       = 0
    release_frames    = 0
    lost_gaze_frames  = 0
    pending_release   = False
    prev_status       = 'NORMAL'
    summary_until     = 0
    ear               = None
    last_idle_log     = 0.0
    # 개인화 EAR 캘리브레이션
    ear_threshold     = EAR_THRESHOLD   # 캘리브레이션 후 개인 값으로 덮어씀
    ear_cal_samples   = []
    ear_cal_done      = False
    EAR_CAL_TARGET    = 30
    # 하품 감지
    yawn_frame_count  = 0
    yawn_in_progress  = False   # 현재 하품 중 (중복 카운트 방지)
    mar               = None

    print('=' * 60)
    print('DrowsyGuard 세션 모드 준비 완료.')
    print('  버튼 = 세션 시작 / 종료')
    print('  Ctrl+C = 프로그램 종료')
    print('=' * 60)

    try:
        while True:
            # ── 버튼: 시작/종료 토글 ───────────────────
            if _button_event.is_set():
                _button_event.clear()
                if session_state == 'IDLE':
                    session = new_session()
                    session_state    = 'ACTIVE'
                    frame_count      = 0
                    release_frames   = 0
                    prev_status      = 'NORMAL'
                    yawn_frame_count = 0
                    yawn_in_progress = False
                    # 캘리브레이션 새로 시작
                    ear_cal_samples  = []
                    ear_cal_done     = False
                    ear_threshold    = EAR_THRESHOLD
                    beep_once()
                    set_led('NORMAL')
                    print(f'[세션 시작] {datetime.now().strftime("%H:%M:%S")} — 캘리브레이션 시작')
                elif session_state == 'ACTIVE':
                    if session['_danger_start'] is not None:
                        session_mark_danger_end(session)
                    session['end'] = time.time()
                    save_session_csv(session)
                    session_state = 'SUMMARY'
                    summary_until = time.time() + 8
                    stop_alarm()
                    all_leds_off()
                    print(f'[세션 종료] {datetime.now().strftime("%H:%M:%S")}')

            # ── IDLE ───────────────────────────────────
            if session_state == 'IDLE':
                show_idle()
                now = time.time()
                if now - last_idle_log >= 5.0:
                    print(f'[IDLE] 버튼 입력 대기중... (버튼 현재상태={button.is_pressed})')
                    last_idle_log = now
                time.sleep(0.1)
                continue

            # ── SUMMARY ────────────────────────────────
            if session_state == 'SUMMARY':
                show_summary(session)
                if time.time() > summary_until:
                    session_state = 'IDLE'
                    session       = None
                    show_idle()
                time.sleep(0.2)
                continue

            # ── STRETCHING ────────────────────────────
            if session_state == 'STRETCHING':
                print('[STRETCHING] 시작 — 환희의 송가 + 가이드 30초')
                stop_alarm()
                all_leds_off()
                run_stretching_routine()
                print('[STRETCHING] 완료 → ACTIVE 복귀')
                session_state    = 'ACTIVE'
                frame_count      = 0
                release_frames   = 0
                pending_release  = False
                prev_status      = 'NORMAL'
                set_led('NORMAL')
                continue

            # ── ACTIVE ─────────────────────────────────
            frame = picam2.capture_array()
            if frame is None:
                time.sleep(0.05)
                continue
            frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
            ear, mar, face_detected = get_face_features(frame)

            # ── 캘리브레이션 (세션 시작 후 30개 샘플 수집) ──
            if not ear_cal_done:
                if ear is not None and face_detected:
                    ear_cal_samples.append(ear)
                show_calibration(len(ear_cal_samples), EAR_CAL_TARGET)
                if len(ear_cal_samples) >= EAR_CAL_TARGET:
                    avg = sum(ear_cal_samples) / len(ear_cal_samples)
                    ear_threshold = max(0.15, avg * 0.75)
                    ear_cal_done  = True
                    print(f'[캘리브레이션 완료] 눈뜸 평균 EAR={avg:.3f} → 개인 임계값={ear_threshold:.3f}')
                    beep_once()
                time.sleep(0.05)
                continue

            # ── 눈 감김 카운트 ─────────────────────────
            if ear is not None:
                if ear < ear_threshold:
                    frame_count += 1
                else:
                    frame_count = max(0, frame_count - 1)

            # ── 하품 감지 ──────────────────────────────
            # 주의: 입 크게 벌리면 dlib이 얼굴 인식 실패해서 MAR=None 자주 나옴
            # → mar이 None인 동안엔 카운트 유지 (입 닫고 인식 성공했을 때만 리셋)
            if mar is not None:
                if mar >= MAR_THRESHOLD:
                    yawn_frame_count += 1
                    if yawn_frame_count >= YAWN_FRAMES and not yawn_in_progress:
                        session['yawn_count'] += 1
                        yawn_in_progress = True
                        print(f'[하품 감지] 누적 {session["yawn_count"]}회 (MAR={mar:.2f})')
                else:
                    # 입 다물고 얼굴 인식 성공 → 하품 종료
                    yawn_frame_count = 0
                    yawn_in_progress = False
            # mar is None: 얼굴 인식 실패 → 카운트 그대로 유지

            # 하품 중이면 졸음 위험 보너스 (눈감김 카운트에 +5)
            risk_frames = frame_count + (5 if yawn_in_progress else 0)

            risk   = calc_risk(risk_frames, temp, pressure, posture, ear, ear_threshold)
            status = get_status(risk)

            if status == 'DANGER' and prev_status != 'DANGER':
                session_mark_danger_start(session)
            elif status != 'DANGER' and prev_status == 'DANGER':
                session_mark_danger_end(session)

            if status == 'CAUTION' and prev_status == 'NORMAL':
                session_mark_caution(session)

            env_warning = (temp > TEMP_HOT) or (pressure < PRES_LOW)
            set_led(status, env_warning, pending_release)

            # DANGER 중이거나 응시 해제 진행 중이면 알람 유지
            if status == 'DANGER' or pending_release:
                start_alarm(hot=(temp > TEMP_HOT))
            else:
                stop_alarm()
                if status == 'CAUTION' and prev_status == 'NORMAL':
                    beep_once()

            # DANGER 진입하면 응시 해제 모드 켜기 (NORMAL 될 때까지 유지)
            if status == 'DANGER':
                pending_release = True

            if pending_release:
                gaze_ok = (face_detected and ear is not None and ear >= ear_threshold)
                if gaze_ok:
                    release_frames  += 1
                    lost_gaze_frames = 0
                else:
                    # 얼굴 잠깐 못 잡혀도 0.5초까진 봐줌 (깜빡임/미세 움직임)
                    lost_gaze_frames += 1
                    if lost_gaze_frames > 10:
                        release_frames   = 0
                        lost_gaze_frames = 0
                if release_frames >= GAZE_RELEASE_FRAMES:
                    print(f'[해제 완료] 응시 2초 → 스트레칭 진입')
                    session_mark_danger_end(session)
                    stop_alarm()
                    session_state    = 'STRETCHING'
                    pending_release  = False
                    release_frames   = 0
                    lost_gaze_frames = 0
                    prev_status      = status
                    continue
            else:
                release_frames   = 0
                lost_gaze_frames = 0

            elapsed_min = int((time.time() - session['start']) / 60)
            screen      = get_screen()

            if pending_release:
                show_release(release_frames, face_detected)
            elif screen == 'env':
                show_environment(temp, pressure)
            elif screen == 'posture':
                show_posture_eye(ear, frame_count, distance_cm, posture)
            else:
                eye_ratio = (ear / 0.4) if ear is not None else 0.5
                show_dashboard(risk, status, eye_ratio, posture, elapsed_min)

            ear_str  = f'{ear:.3f}' if ear is not None else ' N/A '
            mar_str  = f'{mar:.2f}' if mar is not None else ' N/A'
            yawn_str = f'{session["yawn_count"]}' if session else '-'
            pose_str = ('숙임' if posture['too_close'] else
                        '이탈' if posture['desk_away'] else
                        '흔들' if posture['unstable']  else '양호')
            print(
                f'[ACTIVE] EAR:{ear_str} MAR:{mar_str} 하품:{yawn_str}회 '
                f'cnt:{frame_count:3d} rel:{release_frames:2d} 위험:{risk:3d}% {status:7s} '
                f'T:{temp:4.1f} D:{distance_cm:5.1f} 자세:{pose_str}'
            )

            prev_status = status
            time.sleep(0.05)

    except KeyboardInterrupt:
        print('\n프로그램 종료 중...')
    finally:
        if session_state == 'ACTIVE' and session is not None:
            if session['_danger_start'] is not None:
                session_mark_danger_end(session)
            session['end'] = time.time()
            save_session_csv(session)
            print('진행 중이던 세션 저장 완료')
        picam2.stop()
        stop_alarm()
        all_leds_off()
        oled_clear()
        GPIO.cleanup()
        print('DrowsyGuard 종료')


if __name__ == '__main__':
    main()
