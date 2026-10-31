"""점검 규칙 (AI 미사용).

계산 전에 데이터 결측, 기준연도 불일치, 분류 관행 차이 의심 등을 찾아
'산정 불가' 또는 '추가 확인 필요' 표시를 만든다. 없는 값을 0으로 바꾸지 않는다.
"""
from __future__ import annotations

import math

BASE_YEAR = 2024
FALLBACK_YEAR = 2023
START_YEAR = 2019


def pick_value(row: dict | None, year: int = BASE_YEAR, fallback: int = FALLBACK_YEAR):
    """기준연도 값을 고른다. 없으면 직전 연도로 대체하고 그 사실을 표시한다.

    반환: (값 또는 None, 실제 사용 연도 또는 None, 플래그 목록)
    """
    if row is None:
        return None, None, ["수입 통계 없음(해당 국가가 UN Comtrade에 보고하지 않음)"]
    v = row.get(year)
    if v is not None:
        return v, year, []
    v = row.get(fallback)
    if v is not None:
        return v, fallback, [f"{year}년 자료 없음 → {fallback}년 값으로 대체(기준연도 불일치)"]
    return None, None, [f"{fallback}~{year}년 자료 없음 → 점수 산정 불가"]


def cagr(row: dict | None, end_year: int | None, start_year: int = START_YEAR):
    """연평균 성장률. 시작 연도 값이 없거나 0이면 계산하지 않는다."""
    if row is None or end_year is None:
        return None, ["성장률 산정 불가(자료 없음)"]
    start = row.get(start_year)
    end = row.get(end_year)
    if start is None:
        return None, [f"{start_year}년 자료 없음 → 성장률 산정 불가"]
    if start == 0:
        return None, [f"{start_year}년 값이 0 → 성장률 산정 불가"]
    if end is None:
        return None, ["성장률 산정 불가(종료 연도 자료 없음)"]
    years = end_year - start_year
    if years <= 0:
        return None, ["성장률 산정 불가(기간 오류)"]
    return (end / start) ** (1 / years) - 1, []


def classification_gap(imp_a: dict | None, imp_b: dict | None, threshold: float = 0.01):
    """같은 나라에서 한 품목번호의 수입이 다른 번호의 1% 미만이면 분류 관행 차이를 의심한다."""
    a, _, _ = pick_value(imp_a)
    b, _, _ = pick_value(imp_b)
    if a is None or b is None or b == 0:
        return []
    if a / b < threshold:
        return [f"분류 관행 차이 의심: 해당 품목 수입({a:,.0f}천 달러)이 비교 품목({b:,.0f}천 달러)의 {threshold:.0%} 미만"]
    return []


def spearman(xs: list[float], ys: list[float]):
    """순위상관계수 (동점은 평균 순위)."""
    n = len(xs)
    if n < 3:
        return None

    def ranks(v):
        order = sorted(range(n), key=lambda i: v[i])
        r = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and v[order[j + 1]] == v[order[i]]:
                j += 1
            avg = (i + j) / 2 + 1
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r

    rx, ry = ranks(xs), ranks(ys)
    mx, my = sum(rx) / n, sum(ry) / n
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    vx = math.sqrt(sum((a - mx) ** 2 for a in rx))
    vy = math.sqrt(sum((b - my) ** 2 for b in ry))
    if vx == 0 or vy == 0:
        return None
    return cov / (vx * vy)
