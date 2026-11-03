# 데이터 출처와 이용 조건

## UN Comtrade
- **Source: UN Comtrade.** (https://comtradeplus.un.org) — 공개 미리보기 API(preview)로 2026-10-31~11-01 수집
- 내용: 국가별 수입(상대국 World)과 한국(보고국 410)의 상대국별 수출, HS 1704·1806·1905·190531·190590·2105, 2019·2023·2024년(일부 2025년)
- 가공: 천 달러 단위 반올림, 국가 코드를 ISO3로 변환, 필요한 연도만 추출. `imp_S2*`, `exp_S2*`, `s2_shares.csv`는 여러 품목을 합산·비율 계산한 파생 데이터
- 규모: 원자료 값 약 4,700건(국가×연도×품목×흐름). 비영리 공모전 데모용으로, UN Comtrade 안내의 무료 분석·소량(10만 건 미만) 재배포 범위 안에서 출처를 표기해 공개합니다.
- 이용 조건: UN Comtrade 이용 약관을 따릅니다. 재사용 시 원 출처를 표기하고 UN Comtrade의 약관을 확인하세요.

## World Bank
- **Source: World Bank, World Development Indicators — GDP per capita (current US$), NY.GDP.PCAP.CD** (https://data.worldbank.org/indicator/NY.GDP.PCAP.CD)
- 라이선스: CC BY 4.0. 가장 최근 연도 값(주로 2024년)만 추출

## 롯데 판매 공개 근거 (`lotte_presence.json`)
- 공개 언론 보도(기사 제목·URL 포함, 확인일 2026-10-31). 근거가 없는 국가는 '미확인'이며 미진출을 뜻하지 않습니다.
