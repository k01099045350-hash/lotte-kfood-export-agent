"""Streamlit UI for Lotte K-Food export opportunity agent.

초안: ChatGPT 작성 / 통합·검증: Claude.
"""
import streamlit as st
import pandas as pd

from agent import analyze, list_products, MOCK_MODE, TRACK_LABEL

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
    run = st.button("분석 실행", type="primary", use_container_width=True, disabled=(total == 0))

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
MODE_LABEL = {"openai": "AI 보고서", "offline": "규칙 기반 보고서(API 키 없음)", "offline_fallback": "규칙 기반 보고서(AI 보고서 검증 실패)"}

st.subheader(product["name"])
c1, c2, c3, c4 = st.columns(4)
c1.metric("후보국", meta.get("n_candidates", 0))
c2.metric("HS 시나리오", len(data.get("scenarios", {})))
c3.metric("기준 연도", meta.get("base_year", "-"))
c4.metric("보고서 방식", MODE_LABEL.get(output["mode"], output["mode"]))

if product.get("cold_chain"):
    st.info("냉동 유통(콜드체인) 검토가 필요한 제품입니다.")

name_map = {r["iso3"]: r["country"] for s in data.get("scenarios", {}).values() for r in s.get("ranking", [])}

tabs = st.tabs(["📄 보고서", "🏆 TOP3", "📊 점수표", "🔄 HS 비교", "🎚️ 민감도", "⚠️ 경고·출처"])

with tabs[0]:
    st.markdown(output["report"])
    st.download_button("보고서 다운로드", data=output["report"], file_name="lotte_export_report.md", mime="text/markdown")
    if output.get("number_warnings"):
        st.warning("보고서 수치 재검증 필요: " + ", ".join(output["number_warnings"]))
    else:
        st.success("보고서의 모든 숫자가 계산 결과와 일치합니다.")

with tabs[1]:
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
                        st.write("두 품목번호 모두 상위 10위:", "예" if item["robust"] else "아니오")
                    st.markdown("**선정 근거**")
                    for why in item.get("why", []):
                        st.write("•", why)
                    if item.get("flags"):
                        st.markdown("**주의**")
                        for f in item["flags"]:
                            st.write("⚠️", f)
                    st.markdown("**검토 항목**")
                    for k, v in item.get("review_items", {}).items():
                        st.write(f"{k}: {v}")
                    for ev in item.get("entry_evidence", []):
                        st.markdown(f"[{ev.get('title', '출처')}]({ev.get('url', '')})")

with tabs[2]:
    st.caption("종합 점수 순위와 단순 수입액 순위를 비교합니다. 점수 = 후보국 안에서의 백분위(0~100)를 가중 평균한 값.")
    for hs, scenario in data.get("scenarios", {}).items():
        st.subheader(f"HS {hs} — {product['hs_scenarios'].get(hs, '')}")
        left, right = st.columns([3, 2])
        ranking = pd.DataFrame(scenario.get("ranking", []))
        baseline = pd.DataFrame(scenario.get("baseline_ranking", []))
        with left:
            st.markdown("**종합 점수 순위**")
            if not ranking.empty:
                cols = ["rank", "country", "score", "market_size_usd", "market_cagr", "kr_export_usd", "kr_cagr", "gdp_pc_usd", "flags"]
                view = ranking[[x for x in cols if x in ranking.columns]].copy()
                view["flags"] = view["flags"].apply(lambda x: " / ".join(x))
                view = view.rename(columns={"rank": "순위", "country": "국가", "score": "점수", "market_size_usd": "수입액(USD)",
                                            "market_cagr": "수입 성장률", "kr_export_usd": "한국 수출(USD)", "kr_cagr": "한국 수출 성장률",
                                            "gdp_pc_usd": "1인당 GDP(USD)", "flags": "주의"})
                st.dataframe(view, hide_index=True, use_container_width=True)
        with right:
            st.markdown("**단순 수입액 순위 (비교 기준)**")
            if not baseline.empty:
                st.dataframe(baseline.rename(columns={"rank": "순위", "country": "국가", "market_size_usd": "수입액(USD)"}).drop(columns=["iso3"]),
                             hide_index=True, use_container_width=True)
        if not ranking.empty:
            st.bar_chart(ranking.dropna(subset=["score"]).head(10).set_index("country")["score"])

with tabs[3]:
    if len(product.get("hs_scenarios", {})) < 2:
        st.info("HS 시나리오 비교는 품목번호가 2개 이상인 제품(빼빼로)에서 제공됩니다.")
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
    rows = [{"변경": v.get("changed", ""), "가중치": str(v.get("weights", {})), "TOP3": ", ".join(name_map.get(x, x) for x in v.get("top3", []))}
            for v in s.get("variants", [])]
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)

with tabs[5]:
    st.subheader("경고 및 데이터 한계")
    for w in data.get("warnings", []):
        st.warning(w)
    st.subheader("데이터 출처")
    for src in meta.get("sources", []):
        st.markdown(f"- [{src.get('name', '출처')}]({src.get('url', '')})")
    st.subheader("실행 파라미터")
    st.json(output["args"])
    with st.expander("원본 분석 JSON"):
        st.json(data)
