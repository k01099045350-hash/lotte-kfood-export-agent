"""OpenAI agent with offline fallback and evidence-only reporting.

초안: ChatGPT 작성 / 통합·검증: Claude.
- 요청 해석: OpenAI 함수 호출(키가 있을 때) 또는 규칙 기반 해석
- 계산: calc.run_analysis (AI 미사용)
- 보고서: OpenAI가 작성하되, 계산 JSON에 없는 숫자가 있으면 폐기하고 규칙 기반 보고서로 대체
"""
import json
import os
import re
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
                "product_id": {"type": "string", "enum": ["pepero", "icecream"]},
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

    if any(x in q for x in ["아이스크림", "빙과", "월드콘", "빵빠레"]):
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


def offline_report(result):
    """Deterministic report; never invent numbers."""
    product = result["product"]
    meta = result.get("meta", {})
    lines = [
        f"# {product['name']} 수출 유망국가 분석",
        "",
        f"- 기준 연도: {meta.get('base_year', '미확인')} / 성장률 기간: {meta.get('growth_period', '미확인')}",
        f"- 후보국 수: {_fmt(meta.get('n_candidates'))} ({meta.get('candidate_rule', '')})",
        f"- 적용 가중치: {meta.get('weights', {})}",
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
        if item.get("robust") is not None:
            lines.append(f"- 두 품목번호 모두 상위 10위: {_fmt(item.get('robust'))}")
        lines.append("- 근거:")
        for why in item.get("why", []):
            lines.append(f"  - {why}")
        if item.get("flags"):
            lines.append("- 주의: " + "; ".join(item["flags"]))
        lines.append("- 검토 항목: " + "; ".join(f"{k}: {v}" for k, v in item.get("review_items", {}).items()))
        for ev in item.get("entry_evidence", []):
            lines.append(f"- 판매 근거 출처: {ev.get('title', '')} — {ev.get('url', '')}")

    if not result.get("top3"):
        lines.append("해당 조건의 후보가 없습니다.")

    if len(product.get("hs_scenarios", {})) >= 2:
        c = result.get("comparison", {})
        lines.extend([
            "",
            "## 품목번호(HS) 비교",
            f"- 두 기준 순위상관(Spearman): {_fmt(c.get('spearman'))}",
            f"- 상위 10개국 중 공통 국가 수: {_fmt(c.get('top10_overlap'))}",
            f"- 두 기준 모두 상위권(견고한 후보): {_country_names(c.get('robust_countries', []), result)}",
            f"- 한 기준에서만 상위권(기준에 민감): {_country_names(c.get('scenario_sensitive', []), result)}",
        ])

    lines.extend(["", "## 가중치 민감도 (각 가중치 ±20%)"])
    s = result.get("sensitivity", {})
    lines.append(f"- TOP3 국가 구성 유지: {_fmt(s.get('stable_top3'))} / 순서까지 유지: {_fmt(s.get('order_stable'))}")
    for variant in s.get("variants", []):
        lines.append(f"- {variant.get('changed', '')}: {_country_names(variant.get('top3', []), result)}")

    lines.extend(["", "## 경고 및 추가 확인 필요"])
    for warning in result.get("warnings", []):
        lines.append(f"- {warning}")

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


def verify_numbers(report, result):
    """보고서의 숫자가 계산 JSON(숫자 값 + 문자열 속 숫자)에 있는지 확인.

    가벼운 안전장치이며, 의미까지 검증하지는 않는다.
    """
    values = set()

    def walk(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                walk(k)
                walk(v)
        elif isinstance(obj, list):
            for v in obj:
                walk(v)
        elif isinstance(obj, bool):
            return
        elif isinstance(obj, (int, float)):
            for x in (obj, round(obj, 2), round(obj, 1), round(obj * 100, 1), round(obj * 100, 2)):
                d = _num(str(x))
                if d is not None:
                    values.add(d)
        elif isinstance(obj, str):
            for m in NUM_RE.finditer(obj):
                d = _num(m.group())
                if d is not None:
                    values.add(d)

    walk(result)
    clean = re.sub(r"https?://\S+", "", report)
    clean = re.sub(r"(?m)^#{1,6}\s*\d+\.", "", clean)
    clean = re.sub(r"\b[A-Z]{3}\b", "", clean)
    clean = re.sub(r"[±×]\s*\d+(?:\.\d+)?%?", "", clean)  # 민감도 설명 문구(±20%, ×0.8)
    clean = re.sub(r"(?:TOP|상위)\s*\d+", "", clean)

    suspicious = []
    for match in NUM_RE.finditer(clean):
        d = _num(match.group())
        if d is None or d in values:
            continue
        if d in (Decimal(1), Decimal(2), Decimal(3)):  # 목록 번호
            continue
        suspicious.append(match.group())
    return sorted(set(suspicious))


def _client():
    if not os.getenv("OPENAI_API_KEY"):
        return None
    try:
        from openai import OpenAI
        return OpenAI(timeout=45, max_retries=1)
    except Exception:
        return None


def _generate_report(client, result):
    response = client.responses.create(
        model=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
        instructions=SYSTEM,
        input=[{
            "role": "user",
            "content": "다음 JSON만 근거로 한국어 분석 보고서를 작성하라.\n"
                       + json.dumps(result, ensure_ascii=False, default=str),
        }],
    )
    return response.output_text


def analyze(query="", product_id="pepero", track="all", weights=None):
    """Main interface for Streamlit."""
    args = parse_request(query, product_id, track, weights)
    client = _client()
    tool_error = None

    if client and (query or "").strip():
        try:
            response = client.responses.create(
                model=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
                instructions=(
                    "사용자 요청을 해석하여 run_analysis 도구를 정확히 한 번 호출하라. "
                    "명시되지 않은 값은 아래 기본값을 유지한다: "
                    + json.dumps(args, ensure_ascii=False)
                ),
                input=query,
                tools=TOOLS,
                tool_choice={"type": "function", "name": "run_analysis"},
            )
            for item in response.output:
                if item.type == "function_call" and item.name == "run_analysis":
                    proposed = json.loads(item.arguments)
                    if proposed.get("product_id") in ("pepero", "icecream"):
                        args["product_id"] = proposed["product_id"]
                    if proposed.get("track") in ("all", "new", "expand"):
                        args["track"] = proposed["track"]
                    pw = proposed.get("weights", {})
                    if isinstance(pw, dict) and all(
                        isinstance(pw.get(k), (int, float)) and pw[k] >= 0 for k in DEFAULT_WEIGHTS
                    ) and sum(pw.values()) > 0:
                        args["weights"] = pw
                    break
        except Exception as exc:
            tool_error = f"요청 해석 실패, 규칙 기반 해석 사용: {exc}"

    result = run_analysis(**args)
    if tool_error:
        result.setdefault("warnings", []).append(tool_error)

    mode = "offline"
    report = offline_report(result)

    if client:
        try:
            candidate = _generate_report(client, result)
            invalid = verify_numbers(candidate, result)
            if invalid:
                result.setdefault("warnings", []).append(
                    "AI 보고서에 계산 결과에 없는 숫자가 있어 폐기함: " + ", ".join(invalid)
                )
                report = offline_report(result)
                mode = "offline_fallback"
            else:
                report = candidate
                mode = "openai"
        except Exception as exc:
            result.setdefault("warnings", []).append(f"OpenAI 보고서 생성 실패: {exc}")
            report = offline_report(result)
            mode = "offline_fallback"

    return {
        "args": args,
        "data": result,
        "report": report,
        "mode": mode,
        "number_warnings": verify_numbers(report, result),
        "mock": MOCK_MODE,
    }


if __name__ == "__main__":
    import sys
    out = analyze(" ".join(sys.argv[1:]))
    print(out["report"])
    print("\n[mode]", out["mode"], "[숫자 검증 경고]", out["number_warnings"])
