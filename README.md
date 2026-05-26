# 📚 DrowsyGuard

> **실시간 다중 신호 기반 집중력 관리 시스템**
>
> 단순 졸음 감지를 넘어, 학습자가 자신의 집중 패턴을 데이터로 인식하고 능동적으로 회복할 수 있는 동반 시스템

---

## 🎯 프로젝트 컨셉

### 문제 인식
- 공부 중 졸음 → 깨우기만 해선 다시 졸기 쉬움
- 자기가 얼마나 졸았는지 **객관적 데이터 부재**
- 환경(온도/공기)이 졸음에 큰 영향이지만 인지 못함

### 해결 방향
1. **3축 융합** 졸음 감지 (눈 + 환경 + 자세)
2. 졸음 → 해제 → **스트레칭 + 클래식 음악** 능동 회복
3. **세션 기반 운영** + **CSV 기록** + **웹 대시보드**로 자기 데이터화

---

## 🔧 하드웨어 구성 (Raspberry Pi 5)

| 부품 | GPIO 핀 | 역할 |
|---|---|---|
| Pi Camera (OV5647) | CSI | 얼굴/눈/입 실시간 감지 |
| OLED (SSD1306) | I2C: GPIO 2/3 | 위험도/환경/자세 표시 |
| BMP180 | I2C: GPIO 2/3 | 온도·기압 측정 |
| HC-SR04 초음파 | GPIO 5/6 (분압 필수) | 얼굴-모니터 거리 |
| Passive 부저 (3핀) | GPIO 18 (PWM) | 단계별 경보 + 클래식 음악 |
| 버튼 | GPIO 17 | 세션 시작/종료 |
| 조이스틱 SW | GPIO 27 | OLED 3화면 전환 |
| LED ×3 (초록/노랑/빨강) | GPIO 22/23/24 | 단계별 시각화 |

---

## 🧠 핵심 알고리즘

### 위험도 계산 (0~100%)
```
위험도 = (눈감김 점수(0~70) + 환경 점수(0~30) + 자세 점수(0~30)) × 시간대 가중치

가중치:
- 고개 숙임 + 눈 감김 동시 → 눈 점수 × 1.3
- 하품 중 → 눈 카운트 +5 보너스
- 시간대별 (점심 후 1.20 / 새벽 1.30 / 늦은 밤 1.15)
```

### 상태머신
```
IDLE  ─[버튼]→  ACTIVE  ─[졸음 누적]→  DANGER  ─[2초 응시]→  STRETCHING
  ↑                                                              │
SUMMARY ←──[버튼]── (CSV 저장) ←─────── ACTIVE ←─[완료]─────────┘
```

---

## ✨ 핵심 기능

### 1. 단계별 LED + 부저 경보
- 🟢 NORMAL → 정상
- 🟢🟡 NORMAL + 환경 경고 → 초록+노랑 동시
- 🟡 CAUTION → 단발 beep (440Hz)
- 🔴 DANGER → 빠른 반복 경보 (880Hz × 3)
- 🌡️ 환경 위험 시 → **낮은 톤(330Hz)** 으로 차별화

### 2. 카메라 응시 해제 (능동적 깨움)
- DANGER 진입 → 카메라 **정면 2초 응시**해야 해제
- OLED 진행바 시각 피드백
- 깜빡임/잠깐 끊김 0.5초 허용

### 3. 스트레칭 가이드 + 클래식 음악 🎵
| 단계 | 행동 | 배경 음악 |
|---|---|---|
| 1 | 목 돌리기 (10초) | 베토벤 - 환희의 송가 |
| 2 | 기지개 켜기 (10초) | 모차르트 - 작은 별 |
| 3 | 물 한 모금 (10초) | 사운드 오브 뮤직 - 에델바이스 |

### 4. 세션 기반 운영
- 버튼 → 시작/종료 토글
- 종료 시 OLED 요약 8초 + CSV 자동 저장

### 5. 개인화 EAR 캘리브레이션
- 세션 시작 후 3초 동안 본인 눈 EAR 평균 측정
- 평균의 75%를 개인 졸음 임계값으로 자동 설정
- 눈 큰 사람/작은 사람 모두 정확하게 감지

### 6. 시간대별 위험 가중치
| 시간대 | 가중치 | 이유 |
|---|---|---|
| 13~16시 | × 1.20 | 식곤증 |
| 2~6시 | × 1.30 | 새벽 (최고 위험) |
| 22시~2시 | × 1.15 | 늦은 밤 |

### 7. 하품 감지 (MAR)
- 입 벌림 비율 (Mouth Aspect Ratio) 측정
- MAR ≥ 0.35 → 하품 1회 카운트
- 하품 시 졸음 위험도 자동 가산

### 8. CSV 자기 데이터 누적
```csv
date,start,end,duration_min,caution_count,danger_count,danger_min,yawn_count
2026-05-27,22:00:25,22:30:53,30.46,5,2,3.40,3
```

### 9. 웹 대시보드 (Flask + Chart.js)
- 스마트폰/노트북에서 `http://<라즈베리파이IP>:8080` 접속
- **카드**: 오늘 공부 시간 / 횟수 / 위험 / 주의
- **일별 차트**: 최근 7일 막대 + 위험 라인
- **시간대별 차트**: 어느 시간에 가장 졸렸는지
- **최근 세션 표**: 10개
- 30초 자동 새로고침

### 10. 조이스틱 3화면 전환 (OLED)
- **Dashboard**: 위험도 % + 경과시간 + 자세
- **Environment**: 온도/기압 + 환기 권장
- **Posture & Eye**: EAR + 거리 + 자세 판정

---

## 📦 설치 및 실행

### 1. 필수 패키지 설치 (Raspberry Pi 5 + Bookworm)

```bash
# 시스템 패키지
sudo apt update
sudo apt install -y python3-venv python3-pip fonts-nanum \
                    python3-picamera2 i2c-tools

# I2C 활성화
sudo raspi-config   # → Interface Options → I2C → Enable

# 가상환경 + Python 패키지
python3 -m venv ~/drowsy_env --system-site-packages
source ~/drowsy_env/bin/activate
pip install opencv-python dlib imutils smbus2 \
            gpiozero rpi-lgpio luma.oled flask
```

### 2. dlib 모델 다운로드

```bash
wget http://dlib.net/files/shape_predictor_68_face_landmarks.dat.bz2
bunzip2 shape_predictor_68_face_landmarks.dat.bz2
```

### 3. RPi.GPIO → rpi-lgpio 교체 (Pi 5 호환)

```bash
pip uninstall -y RPi.GPIO   # 시스템 user-site에도 없는지 확인
pip install rpi-lgpio
```

### 4. 실행

```bash
# 메인 시스템
python3 main.py

# 웹 대시보드 (별도 터미널)
python3 dashboard.py
```

### 5. 접속

- **OLED**: 라즈베리파이에 연결된 화면
- **웹 대시보드**: 스마트폰 브라우저에서 `http://<라즈베리파이IP>:8080`

---

## 🎬 사용 흐름

1. 부팅 → OLED "버튼을 눌러 공부를 시작하세요" 🟢
2. **버튼 누름** → "삐~" + 캘리브레이션 (3초 정면 응시)
3. ACTIVE 시작 → 초록 LED + 대시보드 화면
4. (선택) BMP180에 입김 → 27°C 초과 → 🟡 노랑 LED 동시 + OLED "환기 권장"
5. 졸음 → 🟡 CAUTION (beep) → 🔴 DANGER (반복 경보)
6. 카메라 응시 → 진행바 차오름 (빨강 LED + 부저 유지)
7. 2초 도달 → 🎵 환희의 송가 + OLED "목 돌리기 10초"
8. 작은 별 → 에델바이스로 자동 진행 (30초)
9. 자동으로 학습 복귀 → 🟢 초록
10. **버튼 다시** → 세션 요약 8초 → CSV 자동 저장 → IDLE
11. 스마트폰에서 `http://<IP>:8080` → 누적 데이터 시각화 📊

---

## 🎨 차별점 (다른 졸음 감지와 비교)

| 항목 | 일반 졸음 감지 | DrowsyGuard |
|---|---|---|
| 감지 신호 | 눈만 | 눈 + 환경 + 자세 + 하품 + 시간대 |
| 경보 해제 | 버튼 한 번 | 카메라 2초 응시 (능동) |
| 해제 후 | 무동작 | 스트레칭 + 클래식 음악 |
| 데이터 | 없음 | CSV + 웹 대시보드 |
| 개인화 | 일률 임계값 | 개인별 EAR 자동 캘리브레이션 |
| 환경 인지 | 없음 | 온도/기압별 경보 톤 차별화 |
| UI | 단일 | OLED 3화면 + 웹 대시보드 |

---

## 💻 기술 스택

| 영역 | 사용 기술 |
|---|---|
| **언어** | Python 3.13 |
| **CV / 얼굴** | OpenCV + dlib (68 landmarks, EAR + MAR) |
| **카메라** | Picamera2 (libcamera) |
| **GPIO** | gpiozero + rpi-lgpio |
| **OLED** | luma.oled (SSD1306) |
| **I2C** | smbus2 (BMP180) |
| **웹** | Flask + Chart.js |
| **OS** | Raspberry Pi OS Bookworm |
| **하드웨어** | Raspberry Pi 5 |

---

## 🚀 향후 발전 방향

| 우선순위 | 항목 | 효과 |
|---|---|---|
| ⭐⭐⭐ | 머리 자세 추정 (Head Pose) | 고개 끄덕임/좌우 흔들림 |
| ⭐⭐⭐ | 텔레그램/카카오 알림 봇 | 보호자/스터디메이트 연동 |
| ⭐⭐ | 조도 센서 (BH1750) | 방 밝기까지 졸음 신호 반영 |
| ⭐⭐ | 음성 명령 (Vosk) | "DrowsyGuard 시작" |
| ⭐ | 3D 프린트 케이스 | 제품화 |
| ⭐ | PCB 제작 | 안정성 + 양산성 |

---

## 📁 프로젝트 구조

```
DrowsyGuard/
├── README.md
├── main.py              # 메인 시스템 (감지 + 경보 + 세션)
├── dashboard.py         # Flask 웹 대시보드
├── requirements.txt     # Python 의존성
├── .gitignore
└── shape_predictor_68_face_landmarks.dat   # dlib 모델 (별도 다운로드)
```

---

## 📜 라이선스

MIT License

---

> 💡 캡스톤 프로젝트 (2026)
