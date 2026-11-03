"""제과·빙과 품목군(HS 1704·1806·1905·2105) 합산 데이터 생성 (AI 미사용).

규칙
- 수입: 한 국가·연도에서 모든 품목이 보고된 경우에만 합산한다. 하나라도 없으면 그 연도는 결측.
- 한국 수출: 한국이 보고한 통계이므로 기록이 없는 품목은 0으로 본다(기존 규칙과 동일).
- 같은 연도·같은 단위(천 달러)·같은 무역 흐름만 더한다. 성장률은 합산 금액으로 다시 계산한다(calc.py).
"""
import csv
from pathlib import Path

DATA = Path(__file__).resolve().parent / "data"
YEARS = ["v2019", "v2023", "v2024", "v2025"]
USE_YEARS = {"v2019", "v2023", "v2024"}  # 2025는 일부 품목만 수집되어 합산하지 않음
GROUPS = {"S2": ["1704", "1806", "1905", "210500"], "S2x1905": ["1704", "1806", "210500"]}


def load(prefix, code):
    with open(DATA / f"{prefix}_{code}.csv", encoding="utf-8") as f:
        return {r["iso3"]: r for r in csv.DictReader(f)}


def build(name, codes):
    for prefix, need_all in (("imp", True), ("exp", False)):
        tables = [load(prefix, c) for c in codes]
        isos = sorted(set().union(*tables))
        with open(DATA / f"{prefix}_{name}.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["iso3"] + YEARS)
            for iso in isos:
                out = []
                for y in YEARS:
                    if y not in USE_YEARS:
                        out.append("")
                        continue
                    vals = [t.get(iso, {}).get(y, "") for t in tables]
                    if need_all and any(v in ("", None) for v in vals):
                        out.append("")
                    elif not need_all and all(v in ("", None) for v in vals):
                        out.append("")
                    else:
                        out.append(str(round(sum(float(v) for v in vals if v not in ("", None)))))
                if any(out):
                    w.writerow([iso] + out)


def shares():
    """국가별 2024년(없으면 2023년) 수입 구성비."""
    codes = GROUPS["S2"]
    tables = [load("imp", c) for c in codes]
    with open(DATA / "s2_shares.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["iso3", "year"] + codes)
        for iso in sorted(set().union(*tables)):
            for y in ("v2024", "v2023"):
                vals = [t.get(iso, {}).get(y, "") for t in tables]
                if all(v not in ("", None) for v in vals):
                    tot = sum(float(v) for v in vals)
                    if tot > 0:
                        w.writerow([iso, y[1:]] + [round(100 * float(v) / tot, 1) for v in vals])
                    break


if __name__ == "__main__":
    for n, c in GROUPS.items():
        build(n, c)
    shares()
    print("done")
