"""계산 도구 (AI 미사용).

공개 통계로 국가별 점수와 순위를 계산한다. 같은 입력이면 항상 같은 결과가 나온다.
에이전트(agent.py)는 이 모듈의 run_analysis 결과만 근거로 보고서를 쓴다.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

import checks
from checks import BASE_YEAR, START_YEAR

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"

DEFAULT_WEIGHTS = {"market": 0.45, "korea": 0.40, "gdp": 0.15}
TOP_IMPORTERS = 30
TOP_KR_DEST = 20
EXCLUDE = {"KOR"}
# 중계무역 비중이 큰 국가: 수입액에 재수출 물량이 섞일 수 있음
TRADE_HUBS = {"NLD", "BEL", "SGP", "HKG", "ARE"}

SOURCES = [
    {"name": "UN Comtrade 공개 API (국가별 수입, 한국 수출)", "url": "https://comtradeapi.un.org/public/v1/preview/C/A/HS"},
    {"name": "World Bank – 1인당 GDP (NY.GDP.PCAP.CD)", "url": "https://data.worldbank.org/indicator/NY.GDP.PCAP.CD"},
]


# ---------- 데이터 읽기 ----------

def _load_trade(path: Path) -> dict[str, dict[int, float]]:
    """iso3 -> {연도: 값(천 달러)}. 빈 칸은 결측으로 남긴다(0으로 바꾸지 않음)."""
    out: dict[str, dict[int, float]] = {}
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            out[r["iso3"]] = {int(k[1:]): float(v) for k, v in r.items() if k != "iso3" and v not in ("", None)}
    return out


def _load_gdp() -> dict[str, tuple[int, float]]:
    out = {}
    with open(DATA / "gdp_per_capita.csv", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            out[r["iso3"]] = (int(r["year"]), float(r["gdp_pc_usd"]))
    return out


def _load_json(name: str):
    with open(DATA / name, encoding="utf-8") as f:
        return json.load(f)


def _products():
    with open(ROOT / "products.json", encoding="utf-8") as f:
        return json.load(f)


def list_products() -> dict[str, str]:
    return {k: v["name"] for k, v in _products().items()}


# ---------- 점수 계산 ----------

def _percentile(values: dict[str, float | None]) -> dict[str, float | None]:
    """후보국 안에서의 백분위(0~100). 결측은 결측으로 둔다."""
    present = {k: v for k, v in values.items() if v is not None}
    n = len(present)
    if n == 0:
        return {k: None for k in values}
    order = sorted(present, key=lambda k: present[k])
    pct = {}
    i = 0
    while i < n:
        j = i
        while j + 1 < n and present[order[j + 1]] == present[order[i]]:
            j += 1
        p = 100.0 * ((i + j) / 2) / (n - 1) if n > 1 else 100.0
        for k in range(i, j + 1):
            pct[order[k]] = p
        i = j + 1
    return {k: pct.get(k) for k in values}


def _normalize_weights(w: dict | None) -> dict[str, float]:
    w = dict(DEFAULT_WEIGHTS if not w else w)
    for k in DEFAULT_WEIGHTS:
        w.setdefault(k, 0.0)
        w[k] = max(0.0, float(w[k]))
    s = sum(w[k] for k in DEFAULT_WEIGHTS)
    if s == 0:
        return dict(DEFAULT_WEIGHTS)
    return {k: w[k] / s for k in DEFAULT_WEIGHTS}


def _candidates(imp, exp) -> list[str]:
    def size(iso):
        v, _, _ = checks.pick_value(imp.get(iso))
        return v
    importers = sorted([k for k in imp if k not in EXCLUDE and size(k)], key=lambda k: -size(k))[:TOP_IMPORTERS]
    dests = sorted([k for k in exp if k not in EXCLUDE and exp[k].get(BASE_YEAR)], key=lambda k: -exp[k][BASE_YEAR])[:TOP_KR_DEST]
    return sorted(set(importers) | set(dests))


def _scenario(hs: str, candidates: list[str], weights: dict[str, float], names, gdp, extra_flags=None):
    imp = _load_trade(DATA / f"imp_{hs}.csv")
    exp = _load_trade(DATA / f"exp_{hs}.csv")
    extra_flags = extra_flags or {}

    rows = {}
    for iso in candidates:
        flags = list(extra_flags.get(iso, []))
        if iso in TRADE_HUBS:
            flags.append("중계무역 거점: 수입액에 재수출 물량이 포함될 수 있어 실제 소비 시장보다 크게 보일 수 있음")
        m_size, m_year, f1 = checks.pick_value(imp.get(iso))
        m_cagr, f2 = checks.cagr(imp.get(iso), m_year) if m_size is not None else (None, [])
        flags += f1 + f2
        er = exp.get(iso, {})
        k_exp = er.get(BASE_YEAR)
        if k_exp is None:
            k_exp = 0.0  # 한국 수출 통계는 한국이 보고하므로, 기록이 없으면 해당 연도 수출 실적이 없는 것으로 본다
            flags.append(f"{BASE_YEAR}년 한국 수출 실적 없음(신규 진출 신호일 수 있음)")
        k_cagr, f3 = checks.cagr(er if er else None, BASE_YEAR)
        if er and er.get(START_YEAR) in (None, 0):
            f3 = [f"한국 수출 성장률 산정 불가({START_YEAR}년 실적 없음)"]
        flags += [x for x in f3 if "자료 없음" not in x or er]
        g = gdp.get(iso)
        if g is None:
            flags.append("1인당 GDP 자료 없음")
        rows[iso] = {
            "iso3": iso,
            "country": names.get(iso, iso),
            "market_size_usd": None if m_size is None else m_size * 1000,
            "market_year": m_year,
            "market_cagr": m_cagr,
            "kr_export_usd": k_exp * 1000,
            "kr_cagr": k_cagr,
            "gdp_pc_usd": None if g is None else g[1],
            "flags": flags,
        }

    p_size = _percentile({k: r["market_size_usd"] for k, r in rows.items()})
    p_mcagr = _percentile({k: r["market_cagr"] for k, r in rows.items()})
    p_kexp = _percentile({k: r["kr_export_usd"] for k, r in rows.items()})
    p_kcagr = _percentile({k: r["kr_cagr"] for k, r in rows.items()})
    p_gdp = _percentile({k: r["gdp_pc_usd"] for k, r in rows.items()})

    def mean(*xs):
        xs = [x for x in xs if x is not None]
        return sum(xs) / len(xs) if xs else None

    ranked, excluded = [], []
    for iso, r in rows.items():
        comp = {
            "market": mean(p_size[iso], p_mcagr[iso]) if p_size[iso] is not None else None,
            "korea": mean(p_kexp[iso], p_kcagr[iso]),
            "gdp": p_gdp[iso],
        }
        r["components"] = {k: None if v is None else round(v, 1) for k, v in comp.items()}
        if comp["market"] is None:
            r["score"] = None
            r["flags"].append("시장 수요 지표 없음 → 점수 산정 불가")
            excluded.append(r)
            continue
        avail = {k: weights[k] for k in comp if comp[k] is not None}
        s = sum(avail.values())
        r["score"] = round(sum(comp[k] * avail[k] for k in avail) / s, 2)
        if len(avail) < 3:
            r["flags"].append("일부 지표 결측 → 남은 지표로 가중치 재정규화")
        ranked.append(r)

    ranked.sort(key=lambda r: (-r["score"], r["iso3"]))
    for i, r in enumerate(ranked, 1):
        r["rank"] = i
    for r in excluded:
        r["rank"] = None

    baseline = sorted([r for r in rows.values() if r["market_size_usd"] is not None], key=lambda r: (-r["market_size_usd"], r["iso3"]))
    baseline_ranking = [{"iso3": r["iso3"], "country": r["country"], "rank": i, "market_size_usd": r["market_size_usd"]} for i, r in enumerate(baseline, 1)]

    totals = {
        "kr_export_total_usd": sum(v.get(BASE_YEAR, 0) for k, v in exp.items()) * 1000,
    }
    return {"ranking": ranked + excluded, "baseline_ranking": baseline_ranking, "totals": totals}


def _top_isos(scen, n):
    return [r["iso3"] for r in scen["ranking"] if r["rank"] is not None][:n]


def run_analysis(product_id: str, track: str = "all", weights: dict | None = None) -> dict:
    products = _products()
    if product_id not in products:
        raise ValueError(f"지원하지 않는 제품: {product_id} (가능: {', '.join(products)})")
    prod = products[product_id]
    w = _normalize_weights(weights)
    names = _load_json("country_names.json")
    presence = _load_json("lotte_presence.json").get(product_id, {})
    gdp = _load_gdp()
    hs_list = list(prod["hs_scenarios"])

    # 후보국: 각 품목번호 기준 후보의 합집합
    cand = set()
    imps = {hs: _load_trade(DATA / f"imp_{hs}.csv") for hs in hs_list}
    for hs in hs_list:
        cand |= set(_candidates(imps[hs], _load_trade(DATA / f"exp_{hs}.csv")))
    candidates = sorted(cand)

    # 분류 관행 차이 점검 (품목번호가 2개 이상일 때)
    extra = {}
    if len(hs_list) >= 2:
        a, b = hs_list[0], hs_list[1]
        for iso in candidates:
            g = checks.classification_gap(imps[a].get(iso), imps[b].get(iso))
            if g:
                extra.setdefault(iso, []).append(f"[{a}] " + g[0])

    scenarios = {hs: _scenario(hs, candidates, w, names, gdp, extra if hs == hs_list[0] else None) for hs in hs_list}

    # 품목번호 간 비교
    comparison = {"spearman": None, "top10_overlap": None, "robust_countries": [], "scenario_sensitive": []}
    if len(hs_list) >= 2:
        sa, sb = scenarios[hs_list[0]], scenarios[hs_list[1]]
        ra = {r["iso3"]: r["rank"] for r in sa["ranking"] if r["rank"]}
        rb = {r["iso3"]: r["rank"] for r in sb["ranking"] if r["rank"]}
        common = sorted(set(ra) & set(rb))
        comparison["spearman"] = None if len(common) < 3 else round(checks.spearman([ra[k] for k in common], [rb[k] for k in common]), 3)
        ta, tb = set(_top_isos(sa, 10)), set(_top_isos(sb, 10))
        comparison["top10_overlap"] = len(ta & tb)
        comparison["robust_countries"] = sorted(ta & tb, key=lambda k: (ra[k] + rb[k]))
        comparison["scenario_sensitive"] = sorted([k for k in (ta ^ tb) if (ra.get(k, 999) > 20 or rb.get(k, 999) > 20)])

    # 종합 순위: 품목번호별 순위의 평균 (같은 비중)
    def combined_rank(iso):
        rs = [next((r["rank"] for r in scenarios[hs]["ranking"] if r["iso3"] == iso), None) for hs in hs_list]
        if any(r is None for r in rs):
            return None
        return sum(rs) / len(rs)

    ordered = sorted([i for i in candidates if combined_rank(i) is not None], key=lambda i: (combined_rank(i), i))

    def track_of(iso):
        return "expand" if iso in presence else "unknown"

    if track == "expand":
        pool = [i for i in ordered if track_of(i) == "expand"]
    elif track == "new":
        pool = [i for i in ordered if track_of(i) != "expand"]
    else:
        pool = ordered
    if len(hs_list) >= 2:
        robust = set(comparison["robust_countries"])
        pool = [i for i in pool if i in robust] + [i for i in pool if i not in robust]

    def row(hs, iso):
        return next(r for r in scenarios[hs]["ranking"] if r["iso3"] == iso)

    top3 = []
    for iso in pool[:3]:
        why = []
        for hs in hs_list:
            r = row(hs, iso)
            why.append(
                f"[HS {hs}] 종합 {r['rank']}위(점수 {r['score']}) · 수입 {r['market_size_usd']/1e6:,.1f}백만 달러({r['market_year']}년)"
                + (f", 연평균 성장률 {r['market_cagr']*100:.1f}%" if r['market_cagr'] is not None else ", 성장률 산정 불가")
                + f" · 한국 수출 {r['kr_export_usd']/1e6:,.2f}백만 달러({BASE_YEAR}년)"
                + (f", 한국 수출 성장률 {r['kr_cagr']*100:.1f}%" if r['kr_cagr'] is not None else "")
            )
        flags = sorted({f for hs in hs_list for f in row(hs, iso)["flags"]})
        top3.append({
            "iso3": iso,
            "country": names.get(iso, iso),
            "track": track_of(iso),
            "entry_status": "확인" if iso in presence else "미확인",
            "entry_evidence": presence.get(iso, []),
            "combined_rank": combined_rank(iso),
            "robust": (iso in comparison["robust_countries"]) if len(hs_list) >= 2 else None,
            "why": why,
            "flags": flags,
            "review_items": {k: "미확인" for k in prod["review_items"]},
            "role": "유통 거점 후보 (중계무역 가능성 검토 대상: 재수출 비중·인접국 공급 확인 필요)" if iso in TRADE_HUBS else "소비시장 후보 (현지 최종 수요 확인 필요)",
            "label": "우선 검토 후보",
        })

    # 민감도: 각 가중치를 상대적으로 ±20% 바꾼 뒤 재정규화
    base_top3 = [t["iso3"] for t in top3]
    variants = []
    for k in DEFAULT_WEIGHTS:
        for f in (0.8, 1.2):
            vw = dict(w)
            vw[k] = w[k] * f
            vw = _normalize_weights(vw)
            res = run_analysis_core(product_id, track, vw, candidates, hs_list, names, gdp, presence, extra)
            variants.append({"changed": f"{k} ×{f}", "weights": {x: round(y, 3) for x, y in vw.items()}, "top3": res})
    stable = all(set(v["top3"]) == set(base_top3) for v in variants)  # 구성 국가 기준
    order_stable = all(v["top3"] == base_top3 for v in variants)

    warnings = [prod["hs_note"]]
    if len(hs_list) >= 2:
        warnings.append("빼빼로 결과는 두 품목번호로 각각 계산한 순위의 평균이며, 두 기준 모두 상위 10위 안에 든 국가를 우선 표시한다.")
    if any("대체" in f for hs in hs_list for r in scenarios[hs]["ranking"] for f in r["flags"]):
        warnings.append(f"일부 국가는 {BASE_YEAR}년 수입 자료가 없어 {BASE_YEAR-1}년 값을 사용했다(기준연도 불일치).")
    if any("분류 관행" in f for hs in hs_list for r in scenarios[hs]["ranking"] for f in r["flags"]):
        warnings.append("일부 국가는 품목번호 간 수입 규모 차이가 비정상적으로 커서 분류 관행 차이가 의심된다(해당 국가 표시 참고).")
    warnings.append("네덜란드·벨기에·싱가포르·홍콩·아랍에미리트는 중계무역 거점이라 수입액이 실제 현지 소비보다 클 수 있다.")
    warnings.append("대만은 UN Comtrade에 수입을 보고하지 않아 시장 수요 지표가 없다.")
    warnings.append("롯데 진출 여부는 공개 보도 근거가 있을 때만 '확인'이며, '미확인'은 미진출을 뜻하지 않는다.")
    warnings.append("검토 항목(관세·인증·물류·수익성 등)은 자동 확인되지 않았으며 담당자 확인이 필요하다.")

    return {
        "product": {"id": product_id, **{k: prod[k] for k in ("name", "hs_scenarios", "hs_note", "cold_chain", "review_items")}},
        "meta": {"base_year": BASE_YEAR, "growth_period": f"{START_YEAR}-{BASE_YEAR}", "sources": SOURCES,
                 "n_candidates": len(candidates), "weights": {k: round(v, 3) for k, v in w.items()}, "track": track,
                 "candidate_rule": f"품목번호별 수입 상위 {TOP_IMPORTERS}개국 ∪ 한국 수출 상위 {TOP_KR_DEST}개국의 합집합 (한국 제외)"},
        "scenarios": scenarios,
        "comparison": comparison,
        "top3": top3,
        "sensitivity": {"stable_top3": stable, "order_stable": order_stable, "base_top3": base_top3, "variants": variants},
        "warnings": warnings,
    }


def run_analysis_core(product_id, track, w, candidates, hs_list, names, gdp, presence, extra):
    """민감도 분석용: 같은 후보국에서 가중치만 바꿔 TOP3 국가만 반환."""
    scen = {hs: _scenario(hs, candidates, w, names, gdp, extra if hs == hs_list[0] else None) for hs in hs_list}

    def cr(iso):
        rs = [next((r["rank"] for r in scen[hs]["ranking"] if r["iso3"] == iso), None) for hs in hs_list]
        return None if any(r is None for r in rs) else sum(rs) / len(rs)

    ordered = sorted([i for i in candidates if cr(i) is not None], key=lambda i: (cr(i), i))
    if track == "expand":
        ordered = [i for i in ordered if i in presence]
    elif track == "new":
        ordered = [i for i in ordered if i not in presence]
    if len(hs_list) >= 2:
        ta = {r["iso3"] for r in scen[hs_list[0]]["ranking"] if r["rank"] and r["rank"] <= 10}
        tb = {r["iso3"] for r in scen[hs_list[1]]["ranking"] if r["rank"] and r["rank"] <= 10}
        robust = ta & tb
        ordered = [i for i in ordered if i in robust] + [i for i in ordered if i not in robust]
    return ordered[:3]


if __name__ == "__main__":
    import sys
    pid = sys.argv[1] if len(sys.argv) > 1 else "pepero"
    tr = sys.argv[2] if len(sys.argv) > 2 else "all"
    res = run_analysis(pid, tr)
    print(f"[{res['product']['name']}] 후보국 {res['meta']['n_candidates']}개, 트랙={tr}")
    for hs, s in res["scenarios"].items():
        top = [f"{r['rank']}.{r['country']}({r['score']})" for r in s["ranking"][:10] if r["rank"]]
        print(f"  HS {hs} TOP10: " + ", ".join(top))
        print(f"  HS {hs} 단순 수입액 TOP5: " + ", ".join(f"{b['rank']}.{b['country']}" for b in s["baseline_ranking"][:5]))
    if res["comparison"]["spearman"] is not None:
        c = res["comparison"]
        print(f"  두 기준 순위상관 {c['spearman']}, TOP10 공통 {c['top10_overlap']}개, 견고 후보 {c['robust_countries']}")
    print("  TOP3:")
    for t in res["top3"]:
        print(f"   - {t['country']} [{t['track']}/{t['entry_status']}] 견고={t['robust']}")
        for w_ in t["why"]:
            print("      ", w_)
    print(f"  민감도: TOP3 유지={res['sensitivity']['stable_top3']}")
