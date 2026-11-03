"""데모 평가 실행 (요청 해석 / 보고서 생성 안정성 / 검증기 자체 / 장애 대응).

사용법 (demo 폴더에서):
    py eval/run_eval.py            # OpenAI 키가 있으면 실제 호출 포함, 없으면 규칙 기반·검증기·장애 대응만
    py eval/run_eval.py --runs 5   # 보고서 생성 반복 횟수(분석별)
결과: eval/results.json
"""
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import agent  # noqa: E402

EVAL = Path(__file__).resolve().parent
DEF = {"market": 0.45, "korea": 0.40, "gdp": 0.15}


def judge(args, exp, ui, query):
    pid_ok_list = [ui["product_id"] if p == "(화면값)" else p for p in exp["product_id"]]
    ok_p = args["product_id"] in pid_ok_list
    ok_t = args["track"] == exp["track"]
    w, ew = args["weights"], exp["weights"]
    tot = sum(w.values())
    if ew == "기본":
        ok_w = all(abs(w[k] - DEF[k]) < 0.01 for k in DEF)
    elif ew == "korea↑":
        ok_w = w["korea"] > DEF["korea"] + 1e-6 and abs(tot - 1) < 0.01
    elif ew == "market↑":
        ok_w = w["market"] > DEF["market"] + 1e-6 and abs(tot - 1) < 0.01
    elif ew == "gdp=0":
        ok_w = w["gdp"] < 1e-6 and abs(tot - 1) < 0.01
    else:
        ok_w = all(abs(w[k] - ew[k]) < 0.01 for k in ew)
    ok_warn = True
    if exp.get("warning"):
        notes = agent.unsupported_notes(query)
        ok_warn = any(exp["warning"] in n for n in notes)
    return {"product": ok_p, "track": ok_t, "weights": ok_w, "warning": ok_warn, "all": ok_p and ok_t and ok_w and ok_warn}


def eval_interpret(client):
    spec = json.load(open(EVAL / "requests.json", encoding="utf-8"))
    ui = spec["ui_default"]
    modes = [("rule", None)] + ([("openai", client)] if client else [])
    out = {}
    for name, cl in modes:
        rows = []
        for r in spec["requests"]:
            args, err = agent.interpret(r["q"], ui["product_id"], ui["track"], dict(ui["weights"]), cl)
            j = judge(args, r["expect"], ui, r["q"])
            rows.append({"id": r["id"], "type": r["type"], "q": r["q"], "got": args, "ok": j, "note": err})
        n = len(rows)
        out[name] = {"n": n, "correct": sum(x["ok"]["all"] for x in rows),
                     "by_field": {f: sum(x["ok"][f] for x in rows) for f in ("product", "track", "weights", "warning")},
                     "by_type": {t: f"{sum(x['ok']['all'] for x in rows if x['type'] == t)}/{sum(1 for x in rows if x['type'] == t)}"
                                 for t in dict.fromkeys(x["type"] for x in rows)},
                     "failures": [{"id": x["id"], "q": x["q"], "got": x["got"], "ok": x["ok"]} for x in rows if not x["ok"]["all"]]}
    return out


def eval_generation(runs):
    rows = []
    for pid in ("confect", "pepero", "icecream"):
        for i in range(runs):
            t0 = time.time()
            o = agent.analyze("", pid)
            am = o["data"].get("ai_meta") or {}
            att = am.get("attempts", [])
            rows.append({"product": pid, "run": i + 1, "mode": o["mode"], "seconds": round(time.time() - t0, 1),
                         "first_pass": bool(att) and not att[0]["problems"],
                         "retried": len(att) > 1, "final_pass": o["mode"] == "openai",
                         "problems_first": att[0]["problems"] if att else None,
                         "problems_last": att[-1]["problems"] if att else None,
                         "error": am.get("warning") if not att else None})
    n = len(rows)
    first_fail = [r for r in rows if not r["first_pass"]]
    return {"n": n,
            "first_pass": sum(r["first_pass"] for r in rows),
            "self_correction_success": f"{sum(r['final_pass'] for r in first_fail)}/{len(first_fail)}",
            "final_ai_report": sum(r["final_pass"] for r in rows),
            "fallback_to_rule": sum(r["mode"] == "offline_fallback" for r in rows),
            "safe_completion": sum(r["mode"] in ("openai", "offline_fallback") for r in rows),
            "avg_seconds": round(sum(r["seconds"] for r in rows) / n, 1) if n else None,
            "runs": rows}


def eval_verifier():
    """합성 시험: 가짜 숫자 / 정확한 단위 변환 / 국가 뒤바꿈 / 지표 뒤바꿈 (각 분석의 TOP3에서 자동 생성)."""
    cases = {"fake": [], "conversion": [], "country_swap": [], "metric_swap": []}
    for pid in ("confect", "pepero", "icecream"):
        d = agent.analyze("", pid)["data"] if not os.getenv("OPENAI_API_KEY") else _offline_data(pid)
        hs0 = list(d["scenarios"])[0]
        row = {r["iso3"]: r for r in d["scenarios"][hs0]["ranking"]}
        t3 = [t["iso3"] for t in d["top3"]]
        name = {i: row[i]["country"] for i in t3}
        for a_i, iso in enumerate(t3):
            r = row[iso]
            other = row[t3[(a_i + 1) % len(t3)]]
            imp_m, exp_m = r["market_size_usd"] / 1e6, r["kr_export_usd"] / 1e6
            cases["fake"].append((d, f"### {name[iso]}\n- 수입 {imp_m * 1.07:,.1f}백만 달러", True))
            cases["conversion"].append((d, f"### {name[iso]}\n- 수입 {r['market_size_usd'] / 1e8:.1f}억 달러", False))
            cases["country_swap"].append((d, f"### {name[iso]}\n- 수입 {other['market_size_usd'] / 1e6:,.1f}백만 달러", True))
            if exp_m > 0.05:
                cases["metric_swap"].append((d, f"### {name[iso]}\n- 한국 수출 {imp_m:,.1f}백만 달러", True))
    out = {}
    for k, lst in cases.items():
        hit = 0
        for d, text, should_flag in lst:
            flagged = bool(agent.verify_numbers(text, d)) or bool(agent.verify_country_links(text, d)["mismatches"]) \
                or bool(agent.verify_country_links(text, d)["metric_mismatches"])
            hit += (flagged == should_flag)
        label = "오탐 없이 통과" if k == "conversion" else "탐지"
        out[k] = {"n": len(lst), "correct": hit, "meaning": label}
    return out


def _offline_data(pid):
    return agent.run_analysis(pid)


class _Resp:
    def __init__(self, text="", output=None):
        self.output_text, self.output = text, output or []


class _Call:
    def __init__(self, args):
        self.type, self.name, self.arguments = "function_call", "run_analysis", json.dumps(args)


def eval_failures():
    """장애 대응: 가짜 클라이언트로 오류 상황을 만들어 규칙 기반 복귀를 확인."""
    tests = []

    def run(label, create, expect_mode, query="빼빼로", expect_warning=None):
        class C:
            class responses:
                pass
        c = C()
        c.responses.create = create
        orig = agent._client
        agent._client = lambda: c
        try:
            o = agent.analyze(query, "confect")
            warns = " ".join(o["data"]["warnings"]) + str((o["data"].get("ai_meta") or {}).get("warning"))
            ok = o["mode"] == expect_mode and not o["number_warnings"] and (expect_warning is None or expect_warning in warns) \
                and o["args"]["product_id"] in ("confect", "pepero", "icecream")
            tests.append({"case": label, "mode": o["mode"], "ok": ok,
                          "warning": (o["data"].get("ai_meta") or {}).get("warning") or next((w for w in o["data"]["warnings"] if "AI" in w or "해석" in w), None)})
        finally:
            agent._client = orig

    def boom(**kw):
        raise RuntimeError("simulated API error")

    def timeout(**kw):
        raise TimeoutError("simulated timeout")

    def bad_args(**kw):
        if kw.get("tools"):
            return _Resp(output=[_Call({"product_id": "chips", "track": "everything", "weights": {"market": -1, "korea": 2, "gdp": 0}})])
        return _Resp("### 1. 미국\n- 수입 7,829.8백만 달러(2024년)")  # 빼빼로(규칙 기반 해석값) 기준 정상 값

    def always_fake(**kw):
        if kw.get("tools"):
            return _Resp(output=[_Call({"product_id": "pepero", "track": "all", "weights": DEF})])
        return _Resp("### 1. 미국\n- 수입 99,999.9백만 달러")

    run("API 오류", boom, "offline_fallback")
    run("시간 초과", timeout, "offline_fallback")
    run("허용 범위 밖 인자(없는 제품·음수 가중치)", bad_args, "openai", expect_warning="허용 범위")
    run("보고서 2회 연속 가짜 숫자", always_fake, "offline_fallback")
    return {"n": len(tests), "correct": sum(t["ok"] for t in tests), "tests": tests}


if __name__ == "__main__":
    runs = int(sys.argv[sys.argv.index("--runs") + 1]) if "--runs" in sys.argv else 5
    client = agent._client()
    res = {"date": datetime.now().strftime("%Y-%m-%d %H:%M"), "model": os.getenv("OPENAI_MODEL", "gpt-4.1-mini") if client else None,
           "openai": bool(client)}
    print("1/4 요청 해석 평가...")
    res["interpret"] = eval_interpret(client)
    print("2/4 검증기 자체 평가...")
    res["verifier"] = eval_verifier()
    print("3/4 장애 대응 평가...")
    res["failures"] = eval_failures()
    if client:
        print(f"4/4 보고서 생성 안정성 평가 (3개 분석 × {runs}회, 몇 분 걸림)...")
        res["generation"] = eval_generation(runs)
    else:
        res["generation"] = None
        print("4/4 OpenAI 키가 없어 보고서 생성 평가는 건너뜀")
    json.dump(res, open(EVAL / "results.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
    print("\n=== 요약 ===")
    for k, v in res["interpret"].items():
        print(f"요청 해석({k}): {v['correct']}/{v['n']}  유형별 {v['by_type']}")
    for k, v in res["verifier"].items():
        print(f"검증기 {k}: {v['correct']}/{v['n']} ({v['meaning']})")
    print(f"장애 대응: {res['failures']['correct']}/{res['failures']['n']}")
    g = res["generation"]
    if g:
        print(f"보고서 생성 {g['n']}회: 1차 통과 {g['first_pass']}, 자기 수정 성공 {g['self_correction_success']}, "
              f"최종 AI 보고서 {g['final_ai_report']}, 규칙 기반 전환 {g['fallback_to_rule']}, 안전 처리 {g['safe_completion']}, 평균 {g['avg_seconds']}초")
    print("결과 파일: eval/results.json")
