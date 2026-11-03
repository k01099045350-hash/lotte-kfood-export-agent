"""OpenAI agent with offline fallback and evidence-only reporting.

초안: ChatGPT 작성 / 통합·검증: Claude.
- 요청 해석: OpenAI 함수 호출(키가 있을 때) 또는 규칙 기반 해석
- 계산: calc.run_analysis (AI 미사용)
- 보고서: OpenAI가 작성하되, 계산 JSON에 없는 숫자가 있으면 폐기하고 규칙 기반 보고서로 대체
"""
import json
import os
import re
import time
from decimal import Decimal, InvalidOperation

try:
    from calc import list_products, run_analysis
    MOCK_MODE = False
except ImportError:
    from mock_calc import list_products, run_analysis
    MOCK_MODE = True

DEFAULT_WEIGHTS = {"market": .45, "korea": .40, "gdp": .15}

TRACK_LABEL = {
    "expand": "기존 시장 확대 (공개 근거로 판매 확인)",
    "unknown": "진출 여부 미확인 (신규 진출 가능성 검토)",
    "new": "신규 진출 검토",
}

SYSTEM = """
당신은 롯데웰푸드 K-푸드 수출 유망국가 분석 에이전트다.
사용 가능한 도구는 list_products와 run_analysis다.

규칙:
1. 숫자와 사실은 run_analysis 결과 JSON만 근거로 한다. JSON에 적힌 숫자를 그대로 옮기고, 새로 계산하거나 반올림하지 않는다.
2. 출처는 JSON의 meta.sources와 entry_evidence만 사용한다.
3. warnings, flags와 미확인 검토 항목을 반드시 보고한다.
4. '진출 가능', '진출 확정' 대신 '우선 검토 후보'라고 표현한다.
5. 빼빼로는 HS 시나리오별 순위 차이, robust/sensitive 국가를 설명한다.
6. 제품 간 점수를 비교하지 않는다.
7. 확인되지 않은 진출 상태를 미진출로 단정하지 않는다.
8. JSON에 없는 관세율, 인증 요건, 물류비 등을 추측하지 않는다.
9. 점수는 교역 기반 시장 기회이며 현지 소비 잠재력과 같지 않다. top3의 role(소비시장 후보/유통 거점 후보)을 함께 설명한다.
10. 아이스크림 수치는 한국 전체 빙과 수출이며 롯데 실적이 아님을 밝힌다.
11. 간결하고 명확한 한국어로 작성한다.
"""

TOOLS = [
    {
        "type": "function",
        "name": "list_products",
        "description": "분석 가능한 롯데웰푸드 제품 목록",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        "strict": True,
    },
    {
        "type": "function",
        "name": "run_analysis",
        "description": "제품별 국가 순위, HS 비교, 민감도 분석",
        "parameters": {
            "type": "object",
            "properties": {
                "product_id": {"type": "string", "enum": ["confect", "pepero", "icecream"]},
                "track": {"type": "string", "enum": ["all", "new", "expand"]},
                "weights": {
                    "type": "object",
                    "properties": {
                        "market": {"type": "number"},
                        "korea": {"type": "number"},
                        "gdp": {"type": "number"},
                    },
                    "required": ["market", "korea", "gdp"],
                    "additionalProperties": False,
                },
            },
            "required": ["product_id", "track", "weights"],
            "additionalProperties": False,
        },
        "strict": True,
    },
]


def parse_request(query, product_id="pepero", track="all", weights=None):
    """Offline parser. UI settings take precedence unless text overrides."""
    q = (query or "").lower()
    w = dict(DEFAULT_WEIGHTS if weights is None else weights)

    if any(x in q for x in ["품목군", "시장 전체", "식품시장", "제과·빙과", "제과 시장", "제과시장"]):
        product_id = "confect"
    elif any(x in q for x in ["아이스크림", "빙과", "월드콘", "빵빠레"]):
        product_id = "icecream"
    elif "빼빼로" in q:
        product_id = "pepero"

    if any(x in q for x in ["신규", "새 시장", "미진출"]):
        track = "new"
    elif any(x in q for x in ["기존", "확대", "기진출"]):
        track = "expand"

    if any(x in q for x in ["한국산", "한국 수출", "수출 실적"]):
        if any(x in q for x in ["높", "중시", "강화", "더 중요"]):
            w["korea"] *= 1.5

    if "시장 규모" in q and any(x in q for x in ["높", "중시", "강화"]):
        w["market"] *= 1.5

    if "gdp" in q or "소득" in q:
        if any(x in q for x in ["높", "중시", "강화"]):
            w["gdp"] *= 1.5
        if any(x in q for x in ["빼고", "제외", "없이"]):
            w["gdp"] = 0.0

    patterns = {
        "market": r"(?:시장|market)\s*(?:비중|가중치)?\s*[:=]?\s*(\d+(?:\.\d+)?)\s*%",
        "korea": r"(?:한국|수출|korea)\s*(?:비중|가중치)?\s*[:=]?\s*(\d+(?:\.\d+)?)\s*%",
        "gdp": r"(?:gdp|소득)\s*(?:비중|가중치)?\s*[:=]?\s*(\d+(?:\.\d+)?)\s*%",
    }
    for key, pattern in patterns.items():
        m = re.search(pattern, q, re.I)
        if m:
            w[key] = float(m.group(1))

    if any(float(v) < 0 for v in w.values()):
        w = dict(DEFAULT_WEIGHTS)
    total = sum(float(v) for v in w.values())
    w = {k: float(v) / total for k, v in w.items()} if total else dict(DEFAULT_WEIGHTS)

    return {"product_id": product_id, "track": track, "weights": w}


UNSUPPORTED = {
    "지역": (["동남아", "아시아", "유럽", "중동", "북미", "남미", "아프리카", "오세아니아"],
            "지역 조건은 아직 지원하지 않아 전체 후보국 기준으로 분석함"),
    "연도": (["2025년 기준", "2023년 기준", "2022년 기준", "최근 3년"], "기준연도·기간 변경은 지원하지 않아 2024년·2019~2024년 기준으로 분석함"),
}


def unsupported_notes(query):
    q = query or ""
    return [msg for words, msg in UNSUPPORTED.values() if any(w in q for w in words)]


def _fmt(value):
    if value is None:
        return "미확인"
    if isinstance(value, bool):
        return "예" if value else "아니오"
    if isinstance(value, float):
        return f"{value:,.3f}".rstrip("0").rstrip(".")
    if isinstance(value, int):
        return f"{value:,}"
    return str(value)


def _country_names(ids, result):
    mapping = {}
    for scenario in result.get("scenarios", {}).values():
        for row in scenario.get("ranking", []):
            mapping[row["iso3"]] = row["country"]
    return ", ".join(mapping.get(x, x) for x in ids) or "없음"


WEIGHT_NAME = {"market": "시장 수요", "korea": "한국산 교역 실적", "gdp": "1인당 GDP"}


def _variant_label(changed):
    """'market ×0.8' → '시장 수요 20% 낮춤'"""
    try:
        key, factor = changed.split(" ×")
        f = float(factor)
        return f"{WEIGHT_NAME.get(key, key)} {abs(round((f - 1) * 100))}% {'낮춤' if f < 1 else '높임'}"
    except ValueError:
        return changed


def offline_report(result):
    """Deterministic report; never invent numbers."""
    product = result["product"]
    meta = result.get("meta", {})
    lines = [
        f"# {product['name']} 수출 유망국가 분석",
        "",
        f"- 기준 연도: {meta.get('base_year', '미확인')} / 성장률 기간: {meta.get('growth_period', '미확인')}",
        f"- 후보국 수: {_fmt(meta.get('n_candidates'))} ({meta.get('candidate_rule', '')})",
        "- 적용 가중치: 시장 수요 {:.0%} / 한국산 교역 실적 {:.0%} / 1인당 GDP {:.0%}".format(
            meta.get("weights", {}).get("market", 0), meta.get("weights", {}).get("korea", 0), meta.get("weights", {}).get("gdp", 0)),
        "",
        "## 우선 검토 후보",
    ]

    for i, item in enumerate(result.get("top3", []), 1):
        lines.extend([
            f"### {i}. {item['country']} — {item.get('label', '우선 검토 후보')}",
            f"- 구분: {TRACK_LABEL.get(item.get('track'), item.get('track'))}",
            f"- 전략적 역할: {item.get('role', '역할 미확인')}",
            f"- 롯데 판매 근거: {item.get('entry_status', '미확인')}",
        ])
        comp = item.get("composition")
        if comp:
            lines.append("- 수입 구성비({}년): 설탕과자 {}% · 초콜릿 {}% · 빵·비스킷 {}% · 아이스크림 {}%".format(
                comp["year"], comp["1704"], comp["1806"], comp["1905"], comp["2105"]))
        if item.get("robust") is not None:
            lines.append(f"- 두 기준 모두 상위 10위: {_fmt(item.get('robust'))}")
        lines.append("- 근거:")
        for why in item.get("why", []):
            lines.append(f"  - {why}")
        if item.get("flags"):
            lines.append("- 주의: " + "; ".join(item["flags"]))
        lines.append("- 검토 항목: " + "; ".join(f"{k}: {v}" for k, v in item.get("review_items", {}).items()))
        ap = item.get("action_plan") or {}
        if ap.get("risks"):
            lines.append("- 확인할 리스크: " + "; ".join(ap["risks"]))
        if ap.get("actions"):
            lines.append("- 권고 후속 확인 행동(규칙 기반 생성·실행 여부 미확인): " + "; ".join(ap["actions"]))
        for ev in item.get("entry_evidence", []):
            lines.append(f"- 판매 근거 출처: {ev.get('title', '')} — {ev.get('url', '')}")

    if not result.get("top3"):
        lines.append("해당 조건의 후보가 없습니다.")

    if len(product.get("hs_scenarios", {})) >= 2:
        c = result.get("comparison", {})
        lines.extend([
            "",
            "## 두 기준 비교" + (" (품목번호 1905.31 vs 1905.90)" if product.get("id") == "pepero" else " (4개 품목 합산 vs 1905 제외)" if product.get("id") == "confect" else ""),
            f"- 두 기준 순위상관(Spearman): {_fmt(c.get('spearman'))}",
            f"- 상위 10개국 중 공통 국가 수: {_fmt(c.get('top10_overlap'))}",
            f"- 두 기준 모두 상위권(견고한 후보): {_country_names(c.get('robust_countries', []), result)}",
            f"- 한 기준에서만 상위권(기준에 민감): {_country_names(c.get('scenario_sensitive', []), result)}",
        ])

    tr = result.get("tracks", {})
    if tr:
        lines.extend(["", "## 갈래별 TOP3"])
        lines.append(f"- 전체: {_country_names(tr.get('all', []), result)}")
        lines.append(f"- 신규 진출 가능성 검토 (롯데 진출 공개 근거 없음, 미진출 확정 아님): {_country_names(tr.get('new', []), result)}")
        lines.append(f"- 기존 시장 확대 (롯데 판매 공개 근거 있음): {_country_names(tr.get('expand', []), result) if tr.get('expand') else '공개 근거가 확인된 국가 없음'}")

    lines.extend(["", "## 가중치 민감도 (각 가중치 ±20%)"])
    s = result.get("sensitivity", {})
    lines.append(f"- TOP3 국가 구성 유지: {_fmt(s.get('stable_top3'))} / 순서까지 유지: {_fmt(s.get('order_stable'))}")
    for variant in s.get("variants", []):
        lines.append(f"- {_variant_label(variant.get('changed', ''))}: {_country_names(variant.get('top3', []), result)}")

    sb = s.get("small_base_tests", [])
    if sb:
        lines.extend(["", "## 소액 기저 기준 민감도"])
        lines.append(f"- 규칙: {s.get('small_base_rule', '')}")
        for t in sb:
            label = "규칙 미적용" if not t.get("threshold_usd") else f"기준 {t['threshold_usd']:,}달러"
            lines.append(f"- {label}: {_country_names(t.get('top3', []), result)}")

    yf = s.get("year_fallback") or {}
    if yf.get("countries"):
        lines.extend(["", "## 2023년 값 대체 국가 민감도"])
        lines.append(f"- 2024년 수입 자료가 없어 2023년 값을 쓴 국가: {_country_names(yf['countries'], result)}")
        lines.append(f"- (고정) 기존 순위에서 해당 국가만 제외: {_country_names(yf.get('fixed_top3') or [], result)}")
        lines.append(f"- (재산정) 후보에서 제외하고 점수 다시 계산: {_country_names(yf.get('recomputed_top3') or [], result)}")
        if yf.get("fixed_top3") != yf.get("recomputed_top3"):
            lines.append("- 재산정에서만 바뀌는 차이는 해당 국가의 경쟁력 때문이 아니라 후보 집합이 바뀌며 상대점수(백분위)가 움직인 결과다.")

    lines.extend(["", "## 경고 및 추가 확인 필요"])
    for warning in result.get("warnings", []):
        lines.append(f"- {warning}")

    if result.get("check_links"):
        lines.extend(["", "## 추가 확인처 (실제 조회 결과가 아니며, 검토 항목은 확인 전까지 '미확인')"])
        for l in result["check_links"]:
            lines.append(f"- {l['name']}: {l['url']}")

    lines.extend(["", "## 출처"])
    for src in meta.get("sources", []):
        lines.append(f"- {src.get('name', '')}: {src.get('url', '')}")

    if MOCK_MODE:
        lines.insert(0, "**[MOCK] 아래 내용은 가상 데이터 기반 시연입니다.**\n")

    return "\n".join(lines)


NUM_RE = re.compile(r"(?<![\dA-Za-z.])-?\d[\d,]*(?:\.\d+)?(?![\d.])")


def _num(raw):
    try:
        return Decimal(raw.replace(",", "")).normalize()
    except InvalidOperation:
        return None


SKIP_KEYS = {"ai_meta"}  # AI 시도 기록(폐기 초안 등)은 검증 근거에서 제외


def _add_number(values, x):
    d = _num(str(x))
    if d is not None:
        values.add(d)


def collect_values(obj, values=None):
    """JSON 안의 모든 숫자(+정확한 단위 변환 표기)를 모은다."""
    values = set() if values is None else values

    def walk(o, key=""):
        if isinstance(o, dict):
            for k, v in o.items():
                if k in SKIP_KEYS:
                    continue
                walk(k)
                walk(v, str(k))
        elif isinstance(o, list):
            for v in o:
                walk(v, key)
        elif isinstance(o, bool):
            return
        elif isinstance(o, (int, float)):
            for x in (o, round(o, 2), round(o, 1), round(o * 100, 1), round(o * 100, 2)):
                _add_number(values, x)
            if key.endswith("_usd"):  # 정확한 단위 변환만 허용: 백만·억·만 달러
                for div, nd in ((1e6, 1), (1e6, 2), (1e8, 1), (1e8, 2), (1e8, 0), (1e4, 0)):
                    _add_number(values, round(o / div, nd) if nd else int(round(o / div)))
        elif isinstance(o, str):
            for m in NUM_RE.finditer(o):
                _add_number(values, m.group())
                d = _num(m.group())
                if d is not None and "백만 달러" in o[m.end():m.end() + 6]:  # 문자열 속 '백만 달러' 값 → 억 달러 표기
                    for nd in (0, 1, 2):
                        _add_number(values, round(float(d) / 100, nd) if nd else int(round(float(d) / 100)))

    walk(obj)
    return values


def _clean_report(report):
    clean = re.sub(r"https?://\S+", "", report)
    clean = re.sub(r"(?m)^#{1,6}\s*\d+\.", "", clean)
    clean = re.sub(r"(?m)^\s*\d+\.\s", "", clean)  # 번호 목록
    clean = re.sub(r"\b[A-Z]{3}\b", "", clean)
    clean = re.sub(r"[±×]\s*\d+(?:\.\d+)?%?", "", clean)  # 민감도 설명 문구(±20%, ×0.8)
    clean = re.sub(r"(?:TOP|상위)\s*\d+", "", clean)
    clean = re.sub(r"\b\d{4}\.\d{2}\b", "", clean)  # 품목번호 표기(예: 1905.31)
    clean = re.sub(r"\d+%\s*(?:낮춤|높임)", "", clean)  # 민감도 설명 문구
    return clean


def verify_numbers(report, result):
    """① 존재 확인: 보고서의 숫자가 계산 JSON 어딘가에 있는지(정확한 단위 변환 포함)."""
    values = collect_values(result)
    suspicious = []
    for match in NUM_RE.finditer(_clean_report(report)):
        d = _num(match.group())
        if d is None or d in values or d in (Decimal(1), Decimal(2), Decimal(3)):
            continue
        suspicious.append(match.group())
    return sorted(set(suspicious))


def _metric_sets(rows):
    """국가 행들에서 지표별 숫자 집합(정확한 단위 변환 포함)."""
    sets = {"import": set(), "export": set(), "score": set(), "rank": set(), "growth": set()}
    for r in rows:
        collect_values({"market_size_usd": r.get("market_size_usd")}, sets["import"]) if r.get("market_size_usd") is not None else None
        collect_values({"kr_export_usd": r.get("kr_export_usd")}, sets["export"]) if r.get("kr_export_usd") is not None else None
        if r.get("score") is not None:
            collect_values(r["score"], sets["score"])
        if r.get("rank") is not None:
            collect_values(r["rank"], sets["rank"])
        for k in ("market_cagr", "kr_cagr"):
            if r.get(k) is not None:
                collect_values(r[k], sets["growth"])
    return sets


def _metric_of(line, start, end):
    """숫자 바로 앞(최대 18자, 이전 숫자 이후)의 지표 단어로 지표를 추정."""
    before = line[max(0, start - 18):start]
    before = re.split(r"\d", before)[-1] if re.search(r"\d", before) else before
    after = line[end:end + 2]
    if "성장률" in before or after.startswith("%"):
        return "growth"
    if "한국 수출" in before or "한국산 수출" in before:
        return "export"
    if "수입" in before:
        return "import"
    if "점수" in before:
        return "score"
    if after.startswith("위"):
        return "rank"
    return None


def verify_country_links(report, result):
    """② 연결 확인(TOP3 한정).

    - 국가 연결: 한 국가만 언급된 줄(또는 그 국가 제목 바로 아래 영역의 줄)의 숫자가 그 국가 값인지 확인
    - 지표 연결: 숫자 앞의 지표 단어(수입·한국 수출·점수·순위·성장률)와 실제 지표가 맞는지 확인
    - 여러 국가가 한 줄에 나오면 판정하지 않고 '판정 제외'로 센다(통과로 간주하지 않음)
    - 한계: 숫자의 존재와 국가·지표 연결만 확인하며, 문장 전체의 의미가 맞는지는 보증하지 않는다.
      단위 변환 허용은 반올림 결과가 같다는 뜻이며 원본과 완전히 같다는 보장은 아니다.
    """
    rows = [r for s in result.get("scenarios", {}).values() for r in s.get("ranking", [])]
    short = {r["country"].split("(")[0]: r["iso3"] for r in rows}
    top3 = {t["iso3"]: t for t in result.get("top3", [])}
    common = collect_values({k: v for k, v in result.items() if k not in ("scenarios", "top3", "composition")})
    per_country, metrics = {}, {}
    for iso in top3:
        crow = [r for r in rows if r["iso3"] == iso]
        vals = collect_values(crow)
        collect_values(top3[iso], vals)
        collect_values(result.get("composition", {}).get(iso, {}), vals)
        per_country[iso] = vals | common
        metrics[iso] = _metric_sets(crow)

    stats = {"checked_lines": 0, "excluded_multi_country": 0, "mismatches": [], "metric_mismatches": []}
    context, context_level = None, 0
    for line in report.splitlines():
        found = {iso for n, iso in short.items() if n and n in line}
        stripped = line.lstrip()
        if stripped.startswith("#"):
            level = len(stripped) - len(stripped.lstrip("#"))
            if len(found) == 1 and next(iter(found)) in top3:
                context, context_level = next(iter(found)), level
            elif context and level <= context_level:
                context = None  # 같은 수준 이상의 새 제목 → 국가 영역 종료
            elif context and found:
                context = None
        if len(found) > 1:
            if any(i in top3 for i in found):
                stats["excluded_multi_country"] += 1
            continue
        iso = next(iter(found)) if found else context
        if iso not in top3:
            continue
        clean = _clean_report(line)
        hits = [m for m in NUM_RE.finditer(clean) if _num(m.group()) not in (None, Decimal(1), Decimal(2), Decimal(3))]
        if not hits:
            continue
        stats["checked_lines"] += 1
        country = next(n for n, i in short.items() if i == iso)
        for m in hits:
            d = _num(m.group())
            if d not in per_country[iso]:
                stats["mismatches"].append(f"{country}: {m.group()}")
                continue
            k = _metric_of(clean, m.start(), m.end())
            ms = metrics[iso]
            if k and d not in ms[k] and any(d in ms[o] for o in ms if o != k):
                other = next(o for o in ms if o != k and d in ms[o])
                names = {"import": "수입", "export": "한국 수출", "score": "점수", "rank": "순위", "growth": "성장률"}
                stats["metric_mismatches"].append(f"{country}: {m.group()}({names[k]}로 표기, 실제는 {names[other]} 값)")
    stats["mismatches"] = sorted(set(stats["mismatches"]))
    stats["metric_mismatches"] = sorted(set(stats["metric_mismatches"]))
    return stats


def _client():
    if not os.getenv("OPENAI_API_KEY"):
        return None
    try:
        from openai import OpenAI
        return OpenAI(timeout=45, max_retries=1)
    except Exception:
        return None


NUMBER_RULE = (
    "숫자 규칙(반드시 지킬 것): 보고서에 쓰는 모든 숫자는 아래 '근거 문장'이나 JSON에 적힌 표기를 글자 그대로 복사한다. "
    "단위 변환(예: 백만 달러→억 달러), 반올림, 합계·차이·비율 계산, 새 숫자 만들기를 하지 않는다. "
    "확신이 없으면 숫자를 빼고 말로 설명한다."
)


def _report_input(result):
    """AI에게 넘길 핵심 자료만 추린다(전체 순위표 대신 TOP3·비교·민감도·경고)."""
    names = {r["iso3"]: r["country"] for s in result.get("scenarios", {}).values() for r in s.get("ranking", [])}
    nm = lambda ids: [names.get(i, i) for i in ids]  # noqa: E731
    s = result.get("sensitivity", {})
    return {
        "product": result.get("product"),
        "meta": result.get("meta"),
        "top3": result.get("top3"),
        "comparison": {**result.get("comparison", {}),
                       "robust_countries": nm(result.get("comparison", {}).get("robust_countries", [])),
                       "scenario_sensitive": nm(result.get("comparison", {}).get("scenario_sensitive", []))},
        "tracks": {k: nm(v) for k, v in result.get("tracks", {}).items()},
        "sensitivity": {"stable_top3": s.get("stable_top3"), "order_stable": s.get("order_stable"),
                        "variants": [{"changed": _variant_label(v["changed"]), "top3": nm(v["top3"])} for v in s.get("variants", [])],
                        "small_base_rule": s.get("small_base_rule"),
                        "small_base_tests": [{"threshold_usd": t["threshold_usd"], "top3": nm(t["top3"])} for t in s.get("small_base_tests", [])]},
        "warnings": result.get("warnings"),
    }


def _generate_report(client, result, feedback=None):
    content = ("다음 JSON만 근거로 한국어 분석 보고서를 마크다운으로 작성하라. "
               "구성: 요약(3줄) → 우선 검토 후보 TOP3(근거·주의·검토 항목) → 갈래별 TOP3 → 기준 비교·민감도 → 한계와 추가 확인 사항 → 출처.\n"
               + NUMBER_RULE + "\n"
               + json.dumps(_report_input(result), ensure_ascii=False, default=str))
    if feedback:
        content += "\n\n" + feedback
    response = client.responses.create(
        model=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
        instructions=SYSTEM + "\n" + NUMBER_RULE,
        input=[{"role": "user", "content": content}],
    )
    return response.output_text


INTERPRET_RULES = """사용자 요청을 해석하여 run_analysis 도구를 정확히 한 번 호출하라.
- product_id: confect=롯데웰푸드 제과·빙과 품목군 시장 전체(과자·초콜릿·비스킷·아이스크림을 묶은 시장), pepero=빼빼로, icecream=아이스크림·빙과(월드콘·빵빠레 등).
  특정 제품명이 없으면 기본값을 유지한다. 제품을 특정하기 어려우면 기본값을 유지한다.
- track: all=전체, new=롯데 진출 공개 근거가 없는 나라(새로 들어갈 곳), expand=이미 판매 중인 나라에서 확대.
- weights(market=시장 수요, korea=한국산 교역 실적, gdp=1인당 GDP): 언급이 없으면 기본값 그대로.
  어떤 항목을 '더 중시'하면 그 항목만 기본값보다 크게 한다. 'GDP는 빼고'면 gdp=0.
  비율을 숫자로 말하면 그 숫자를 쓰고, 말한 숫자의 합이 100이면 언급하지 않은 항목은 0으로 한다.
- 지역 제한(동남아·유럽 등)이나 기준연도 변경처럼 도구가 지원하지 않는 조건은 무시하고 나머지만 반영한다.
"""


def interpret(query="", product_id="pepero", track="all", weights=None, client=None):
    """요청 해석: 규칙 기반 해석 → (키가 있으면) OpenAI 함수 호출로 보정 → 허용 범위 검사."""
    args = parse_request(query, product_id, track, weights)
    tool_error = None
    if client and (query or "").strip():
        try:
            response = client.responses.create(
                model=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
                instructions=INTERPRET_RULES + "\n현재 기본값(규칙 기반 해석 결과): " + json.dumps(args, ensure_ascii=False),
                input=query,
                tools=TOOLS,
                tool_choice={"type": "function", "name": "run_analysis"},
            )
            for item in response.output:
                if item.type == "function_call" and item.name == "run_analysis":
                    proposed = json.loads(item.arguments)
                    rejected = []
                    if proposed.get("product_id") in ("confect", "pepero", "icecream"):
                        args["product_id"] = proposed["product_id"]
                    else:
                        rejected.append(f"product_id={proposed.get('product_id')!r}")
                    if proposed.get("track") in ("all", "new", "expand"):
                        args["track"] = proposed["track"]
                    else:
                        rejected.append(f"track={proposed.get('track')!r}")
                    pw = proposed.get("weights", {})
                    if isinstance(pw, dict) and all(
                        isinstance(pw.get(k), (int, float)) and pw[k] >= 0 for k in DEFAULT_WEIGHTS
                    ) and sum(pw[k] for k in DEFAULT_WEIGHTS) > 0:
                        tot = sum(pw[k] for k in DEFAULT_WEIGHTS)
                        args["weights"] = {k: pw[k] / tot for k in DEFAULT_WEIGHTS}
                    else:
                        rejected.append(f"weights={pw!r}")
                    if rejected:
                        tool_error = "AI가 제안한 값 중 허용 범위를 벗어난 것을 무시하고 규칙 기반 해석값을 사용함: " + ", ".join(rejected)
                    break
        except Exception as exc:
            tool_error = f"요청 해석 실패, 규칙 기반 해석 사용: {exc}"
    return args, tool_error


def analyze(query="", product_id="pepero", track="all", weights=None):
    """Main interface for Streamlit."""
    client = _client()
    args, tool_error = interpret(query, product_id, track, weights, client)

    result = run_analysis(**args)
    if tool_error:
        result.setdefault("warnings", []).append(tool_error)
    for note in unsupported_notes(query):
        result.setdefault("warnings", []).append(note)

    def check(text):
        """존재 확인 + TOP3 국가-숫자 연결 확인. 문제 목록을 돌려준다."""
        missing = verify_numbers(text, result)
        links = verify_country_links(text, result)
        problems = ([f"계산 결과에 없는 숫자 {x}" for x in missing]
                    + [f"다른 국가 값으로 의심({x})" for x in links["mismatches"]]
                    + [f"지표 불일치 의심({x})" for x in links["metric_mismatches"]])
        return problems, missing, links

    mode = "offline"
    report = offline_report(result)
    ai_meta = None

    if client:
        try:
            # 1차 작성 → 검증 → 실패 시 문제를 알려 주고 1회 다시 작성(자기 수정) → 그래도 실패면 폐기
            t0 = time.time()
            candidate = _generate_report(client, result)
            problems, _, links = check(candidate)
            attempts = [{"try": 1, "problems": problems, "link_stats": links}]
            if problems:
                feedback = ("[검증 결과] 직전 보고서에서 다음 문제가 발견됐다: " + "; ".join(problems)
                            + ". 해당 숫자를 JSON에 적힌 그 국가의 값 그대로 바꾸거나 삭제하고 보고서 전체를 다시 작성하라.")
                candidate = _generate_report(client, result, feedback)
                problems, _, links = check(candidate)
                attempts.append({"try": 2, "problems": problems, "link_stats": links})
            ai_meta = {"attempts": attempts, "seconds": round(time.time() - t0, 1),
                       "model": os.getenv("OPENAI_MODEL", "gpt-4.1-mini")}
            if problems:
                ai_meta["rejected_draft"] = candidate
                ai_meta["warning"] = "AI 보고서를 2회 작성했으나 검증을 통과하지 못해 폐기하고 규칙 기반 보고서를 표시함: " + "; ".join(problems)
                report = offline_report(result)
                mode = "offline_fallback"
            else:
                report = candidate
                mode = "openai"
        except Exception as exc:
            ai_meta = {"warning": f"OpenAI 보고서 생성 실패 → 규칙 기반 보고서 표시: {exc}"}
            report = offline_report(result)
            mode = "offline_fallback"
        result["ai_meta"] = ai_meta

    final_missing = verify_numbers(report, result)
    final_links = verify_country_links(report, result)
    return {
        "args": args,
        "data": result,
        "report": report,
        "mode": mode,
        "number_warnings": final_missing,
        "link_check": final_links,
        "mock": MOCK_MODE,
    }


if __name__ == "__main__":
    import sys
    out = analyze(" ".join(sys.argv[1:]))
    print(out["report"])
    print("\n[mode]", out["mode"], "[존재 확인 경고]", out["number_warnings"], "[연결 확인]", out["link_check"])
