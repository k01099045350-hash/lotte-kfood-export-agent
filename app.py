"""Streamlit UI for Lotte K-Food export opportunity agent.

초안: ChatGPT 작성 / 통합·검증: Claude.
"""
import streamlit as st
import pandas as pd

from agent import analyze, list_products, MOCK_MODE, TRACK_LABEL, _variant_label

st.set_page_config(page_title="롯데 K-푸드 수출 에이전트", page_icon="🌏", layout="wide")

st.title("🌏 롯데웰푸드 K-푸드 수출 유망국가 분석")
st.caption("숫자 계산은 Python 코드 · 요청 해석과 보고서 작성은 AI · 근거와 한계를 함께 표시")

if MOCK_MODE:
    st.warning("현재 MOCK 데이터 모드입니다. 모든 수치는 시연용 가상 값입니다.")

products = list_products()

with st.sidebar:
    st.header("분석 설정")
    product_id = st.selectbox("제품 선택", list(products), format_func=lambda x: products[x])
    track = st.selectbox(
        "분석 트랙", ["all", "new", "expand"],
        format_func=lambda x: {"all": "전체", "new": "신규 진출 검토", "expand": "기존 시장 확대"}[x],
    )

    st.subheader("가중치")
    market = st.slider("① 시장 수요 (수입 규모·성장률)", 0, 100, 45)
    korea = st.slider("② 한국산 교역 실적 (한국 수출 규모·성장률)", 0, 100, 40)
    gdp = st.slider("③ 1인당 GDP", 0, 100, 15)
    weights = {"market": market, "korea": korea, "gdp": gdp}
    total = sum(weights.values())
    if total:
        st.caption("정규화 후: 시장 {:.1%} / 한국 {:.1%} / GDP {:.1%}".format(market / total, korea / total, gdp / total))
    else:
        st.error("가중치 합계가 0입니다.")

    query = st.text_area(
        "자연어 요청 (선택)",
        placeholder="예: 아이스크림 기존 시장 확대 후보 찾아줘. 한국 수출 실적을 더 중시해줘.",
        height=110,
    )
    run = st.button("분석 실행", type="primary", width="stretch", disabled=(total == 0))

if run:
    with st.spinner("분석 중..."):
        try:
            st.session_state["analysis"] = analyze(query=query, product_id=product_id, track=track, weights=weights)
        except Exception as exc:
            st.error(f"분석 실패: {exc}")

if "analysis" not in st.session_state:
    st.info("왼쪽에서 제품과 조건을 선택한 뒤 '분석 실행'을 눌러주세요.")
    st.stop()

output = st.session_state["analysis"]
data = output["data"]
product = data["product"]
meta = data.get("meta", {})
MODE_LABEL = {"openai": "AI 작성", "offline": "규칙 기반", "offline_fallback": "규칙 기반(AI 검증 실패)"}

st.subheader(product["name"])
c1, c2, c3, c4 = st.columns(4)
c1.metric("후보국", meta.get("n_candidates", 0))
c2.metric("HS 시나리오", len(data.get("scenarios", {})))
c3.metric("기준 연도", meta.get("base_year", "-"))
c4.metric("보고서 방식", MODE_LABEL.get(output["mode"], output["mode"]))
if output["mode"] == "offline":
    st.caption("OpenAI API 키가 없어 규칙 기반 보고서로 작성했습니다. 숫자는 동일하게 계산 결과에서만 가져옵니다.")

if product.get("cold_chain"):
    st.info("냉동 유통(콜드체인) 검토가 필요한 제품입니다.")

name_map = {r["iso3"]: r["country"] for s in data.get("scenarios", {}).values() for r in s.get("ranking", [])}

tabs = st.tabs(["📄 보고서", "🏆 TOP3", "📊 점수표", "🔄 기준 비교", "🎚️ 민감도", "⚠️ 경고·출처", "🧪 평가 결과"])

with tabs[0]:
    st.markdown(output["report"])
    st.download_button("보고서 다운로드", data=output["report"], file_name="lotte_export_report.md", mime="text/markdown")
    am = data.get("ai_meta") or {}
    if am.get("attempts"):
        st.caption(f"AI 보고서 검증({am.get('model')}, {am.get('seconds')}초): " + " → ".join(
            f"{t['try']}차 {'통과' if not t['problems'] else '문제 ' + str(len(t['problems'])) + '개'}" for t in am["attempts"]))
    if am.get("warning"):
        st.warning(am["warning"])
    if am.get("rejected_draft"):
        with st.expander("폐기된 AI 초안 보기 (검증 실패)"):
            st.markdown(am["rejected_draft"])
    lc = output.get("link_check") or {}
    if output.get("number_warnings") or lc.get("mismatches") or lc.get("metric_mismatches"):
        st.warning("보고서 수치 재검증 필요: " + ", ".join(output.get("number_warnings", []) + lc.get("mismatches", []) + lc.get("metric_mismatches", [])))
    else:
        v1, v2, v3 = st.columns(3)
        v1.success("숫자 존재 확인 통과")
        v2.success(f"TOP3 단일국가 줄 {lc.get('checked_lines', 0)}개 국가·지표 연결 확인 통과")
        v3.info(f"다국가 비교 줄 {lc.get('excluded_multi_country', 0)}개 판정 제외")
        st.caption("검증기는 숫자의 존재와 TOP3 국가·지표 연결만 확인하며, 문장 전체의 의미가 맞는지는 보증하지 않습니다.")

with tabs[1]:
    tr = data.get("tracks", {})
    if tr:
        g1, g2, g3 = st.columns(3)
        g1.markdown("**전체 TOP3**  \n" + (", ".join(name_map.get(x, x) for x in tr.get("all", [])) or "없음"))
        g2.markdown("**신규 진출 가능성 검토**  \n" + (", ".join(name_map.get(x, x) for x in tr.get("new", [])) or "없음")
                    + "  \n<small>롯데 진출 공개 근거 없음 (미진출 확정 아님)</small>", unsafe_allow_html=True)
        g3.markdown("**기존 시장 확대**  \n" + (", ".join(name_map.get(x, x) for x in tr.get("expand", [])) or "공개 근거가 확인된 국가 없음"))
        st.divider()
    top3 = data.get("top3", [])
    if not top3:
        st.info("선택한 조건의 후보국이 없습니다.")
    else:
        cols = st.columns(3)
        for i, item in enumerate(top3):
            with cols[i % 3]:
                with st.container(border=True):
                    st.subheader(f"{i + 1}. {item['country']}")
                    st.caption(item.get("label", "우선 검토 후보"))
                    st.write("구분:", TRACK_LABEL.get(item.get("track"), item.get("track")))
                    st.write("전략적 역할:", item.get("role", "역할 미확인"))
                    st.write("롯데 판매 근거:", item.get("entry_status", "미확인"))
                    if item.get("robust") is not None:
                        st.write("두 기준 모두 상위 10위:", "예" if item["robust"] else "아니오")
                    comp = item.get("composition")
                    if comp:
                        st.caption("수입 구성비({}년): 설탕과자 {}% · 초콜릿 {}% · 빵·비스킷 {}% · 아이스크림 {}%".format(
                            comp["year"], comp["1704"], comp["1806"], comp["1905"], comp["2105"]))
                    st.markdown("**선정 근거**")
                    for why in item.get("why", []):
                        st.write("•", why)
                    if item.get("flags"):
                        st.markdown("**주의**")
                        for f in item["flags"]:
                            st.write("⚠️", f)
                    ap = item.get("action_plan") or {}
                    if ap.get("risks"):
                        st.markdown("**확인할 리스크**")
                        for x in ap["risks"]:
                            st.write("•", x)
                    if ap.get("actions"):
                        st.markdown("**권고 후속 확인 행동** (규칙 기반 생성·실행 여부 미확인)")
                        for x in ap["actions"]:
                            st.write("→", x)
                    st.markdown("**검토 항목**")
                    for k, v in item.get("review_items", {}).items():
                        st.write(f"{k}: {v}")
                    for ev in item.get("entry_evidence", []):
                        st.markdown(f"[{ev.get('title', '출처')}]({ev.get('url', '')})")

with tabs[2]:
    st.caption("종합 점수 순위와 단순 수입액 순위를 비교합니다. 점수 = 후보국 안에서의 백분위(0~100)를 가중 평균한 값.")
    for hs, scenario in data.get("scenarios", {}).items():
        st.subheader(f"{product.get('scenario_short', {}).get(hs, 'HS ' + hs)} — {product['hs_scenarios'].get(hs, '')}")
        left, right = st.columns([3, 2])
        ranking = pd.DataFrame(scenario.get("ranking", []))
        baseline = pd.DataFrame(scenario.get("baseline_ranking", []))
        with left:
            st.markdown("**종합 점수 순위**")
            if not ranking.empty:
                cols = ["rank", "country", "score", "market_size_usd", "market_cagr", "kr_export_usd", "kr_cagr", "kr_cagr_scored", "gdp_pc_usd", "flags"]
                view = ranking[[x for x in cols if x in ranking.columns]].copy()
                view["flags"] = view["flags"].apply(lambda x: " / ".join(x))
                for c in ("market_size_usd", "kr_export_usd"):
                    if c in view:
                        view[c] = view[c].apply(lambda v: None if v is None or v != v else round(v / 1e6, 2))
                for c in ("market_cagr", "kr_cagr"):
                    if c in view:
                        view[c] = view[c].apply(lambda v: None if v is None or v != v else round(v * 100, 1))
                if "kr_cagr_scored" in view:
                    view["kr_cagr_scored"] = view["kr_cagr_scored"].map({True: "반영", False: "미반영"})
                view = view.rename(columns={"rank": "순위", "country": "국가", "score": "점수", "market_size_usd": "수입액(백만 달러)",
                                            "market_cagr": "수입 성장률(%)", "kr_export_usd": "한국 수출(백만 달러)", "kr_cagr": "한국 수출 성장률(%)", "kr_cagr_scored": "한국 성장률 점수 반영",
                                            "gdp_pc_usd": "1인당 GDP(USD)", "flags": "주의"})
                st.dataframe(view, hide_index=True, width="stretch")
        with right:
            st.markdown("**단순 수입액 순위 (비교 기준)**")
            if not baseline.empty:
                st.dataframe(baseline.rename(columns={"rank": "순위", "country": "국가", "market_size_usd": "수입액(USD)"}).drop(columns=["iso3"]),
                             hide_index=True, width="stretch")
        if not ranking.empty:
            st.bar_chart(ranking.dropna(subset=["score"]).head(10).set_index("country")["score"])

with tabs[3]:
    if len(product.get("hs_scenarios", {})) < 2:
        st.info("기준 비교는 기준이 2개인 분석(제과·빙과 품목군: 4개 품목 합산 vs 1905 제외 / 빼빼로: 1905.31 vs 1905.90)에서 제공됩니다.")
    else:
        comp = data.get("comparison", {})
        a, b = st.columns(2)
        a.metric("두 기준 순위상관 (Spearman)", comp.get("spearman"))
        b.metric("상위 10개국 중 공통", comp.get("top10_overlap"))
        st.markdown("**두 기준 모두 상위 10위 (견고한 후보)**")
        st.write(", ".join(name_map.get(x, x) for x in comp.get("robust_countries", [])) or "없음")
        st.markdown("**한 기준에서만 상위 10위이고 다른 기준에서는 20위 밖 (기준에 민감)**")
        st.write(", ".join(name_map.get(x, x) for x in comp.get("scenario_sensitive", [])) or "없음")
        st.warning(product.get("hs_note", ""))

with tabs[4]:
    s = data.get("sensitivity", {})
    if s.get("stable_top3"):
        st.success("각 가중치를 ±20% 바꿔도 TOP3 국가 구성이 유지됩니다." + ("" if s.get("order_stable") else " (순서는 일부 바뀜)"))
    else:
        st.warning("가중치에 따라 TOP3 구성이 달라집니다. 아래 변형별 결과를 확인하세요.")
    rows = [{"변경": _variant_label(v.get("changed", "")), "가중치": str(v.get("weights", {})), "TOP3": ", ".join(name_map.get(x, x) for x in v.get("top3", []))}
            for v in s.get("variants", [])]
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    yf = s.get("year_fallback") or {}
    if yf.get("countries"):
        st.markdown("**2023년 값 대체 국가 민감도**")
        st.dataframe(pd.DataFrame([
            {"실험": "기준(대체 국가 포함)", "TOP3": ", ".join(name_map.get(x, x) for x in s.get("base_top3", []))},
            {"실험": "고정: 기존 순위에서 대체 국가만 제외", "TOP3": ", ".join(name_map.get(x, x) for x in yf.get("fixed_top3") or [])},
            {"실험": "재산정: 후보에서 제외 후 점수 다시 계산", "TOP3": ", ".join(name_map.get(x, x) for x in yf.get("recomputed_top3") or [])},
        ]), hide_index=True, width="stretch")
        st.caption("대체 국가: " + ", ".join(name_map.get(x, x) for x in yf["countries"])
                   + ". 재산정에서만 생기는 차이는 후보 집합이 바뀌며 상대점수가 움직인 결과입니다.")
    sb = s.get("small_base_tests", [])
    if sb:
        st.markdown("**소액 기저 기준 민감도**")
        st.caption(s.get("small_base_rule", ""))
        st.dataframe(pd.DataFrame([{"기준": "규칙 미적용" if not t["threshold_usd"] else f"{t['threshold_usd']:,}달러",
                                    "TOP3": ", ".join(name_map.get(x, x) for x in t["top3"])} for t in sb]),
                     hide_index=True, width="stretch")

with tabs[5]:
    st.subheader("경고 및 데이터 한계")
    for w in data.get("warnings", []):
        st.warning(w)
    if data.get("check_links"):
        st.subheader("추가 확인처 (실제 조회 결과가 아니며, 검토 항목은 확인 전까지 미확인)")
        for l in data["check_links"]:
            st.markdown(f"- [{l['name']}]({l['url']})")
    st.subheader("데이터 출처")
    for src in meta.get("sources", []):
        st.markdown(f"- [{src.get('name', '출처')}]({src.get('url', '')})")
    st.subheader("실행 파라미터")
    st.json(output["args"])
    with st.expander("원본 분석 JSON"):
        st.json(data)

with tabs[6]:
    import json as _json
    from pathlib import Path as _Path
    rp = _Path(__file__).resolve().parent / "eval" / "results.json"
    if not rp.exists():
        st.info("아직 평가를 실행하지 않았습니다. demo 폴더에서 `py eval/run_eval.py`를 실행하세요.")
    else:
        ev = _json.loads(rp.read_text(encoding="utf-8"))
        st.caption(f"실행: {ev['date']} · 모델: {ev.get('model') or '없음(규칙 기반만)'} · 소규모 자체 평가이며 일반적인 모델 정확도를 뜻하지 않습니다.")
        st.markdown("**① 요청 해석** (고정된 요청 20개, 기대값은 실험 전 Claude 초안 → ChatGPT 독립 검토로 확정)")
        st.dataframe(pd.DataFrame([{"해석 방식": "AI(OpenAI 함수 호출)" if k == "openai" else "규칙 기반",
                                    "정답": f"{v['correct']}/{v['n']}", **v["by_type"]} for k, v in ev["interpret"].items()]),
                     hide_index=True, width="stretch")
        st.markdown("**② 검증기 자체 시험** (각 분석 TOP3에서 자동 생성한 합성 문장)")
        names = {"fake": "가짜 숫자 탐지", "conversion": "정확한 단위 변환 통과(오탐 없음)", "country_swap": "국가 뒤바꿈 탐지", "metric_swap": "지표 뒤바꿈 탐지"}
        st.dataframe(pd.DataFrame([{"시험": names.get(k, k), "결과": f"{v['correct']}/{v['n']}"} for k, v in ev["verifier"].items()]),
                     hide_index=True, width="stretch")
        st.markdown("**③ 장애 대응** (API 오류·시간 초과·허용 범위 밖 인자·가짜 숫자 반복)")
        st.dataframe(pd.DataFrame([{"상황": t["case"], "결과": "정상 처리" if t["ok"] else "실패", "동작": t["mode"]} for t in ev["failures"]["tests"]]),
                     hide_index=True, width="stretch")
        g = ev.get("generation")
        st.markdown("**④ AI 보고서 생성 안정성**")
        if g:
            st.dataframe(pd.DataFrame([{"생성 횟수": g["n"], "1차 검증 통과": g["first_pass"], "자기 수정 성공(1차 실패 중)": g["self_correction_success"],
                                        "최종 AI 보고서": g["final_ai_report"], "규칙 기반 전환": g["fallback_to_rule"],
                                        "안전 처리": g["safe_completion"], "평균 소요(초)": g["avg_seconds"]}]), hide_index=True, width="stretch")
        else:
            st.info("OpenAI 키 없이 실행해 보고서 생성 평가는 포함되지 않았습니다.")
