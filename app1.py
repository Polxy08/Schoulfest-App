import json
import os
import random
import time
import copy

import streamlit as st
import pandas as pd

STATE_FILE = "tournament_state.json"

RESET_CODE = "2611"
DRAW_CODE = "0987"
BEAMER_CODE = "1521"

BEAMER_REFRESH_SECONDS = 10

CONFIG = {
    "yeargroups": [
        {"name": "7e", "classes": ["C1", "C2", "C3", "G"]},
        {"name": "6e", "classes": ["C1", "C2", "C3", "G"]},
        {"name": "5e", "classes": ["C1", "C3", "C3", "G"]},
        {"name": "4e", "classes": ["C1", "C2", "C3"]},
        {"name": "3e", "classes": ["BC", "D", "G"]},
    ],
    "points": {"win": 2, "draw": 1, "loss": 0},
}


# -----------------------------
# State IO
# -----------------------------
def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def team_id(y_name, cls):
    return f"{y_name} - {cls}"


# -----------------------------
# UI helpers
# -----------------------------
def group_selectbox(key, home, away, current, disabled=False):
    options = ["-", f"{home} gewënnt", "Gläichstand", f"{away} gewënnt"]
    mapping = {None: 0, "H": 1, "D": 2, "A": 3}
    inv = {0: None, 1: "H", 2: "D", 3: "A"}
    idx = mapping.get(current, 0)
    sel = st.selectbox("", options, index=idx, key=key, disabled=disabled)
    return inv[options.index(sel)]


def ko_selectbox(key, p1, p2, current, disabled=False):
    ready = bool(p1 and p2)
    options = ["-", p1 or "-", p2 or "-"]

    idx = 0
    if ready and current == p1:
        idx = 1
    elif ready and current == p2:
        idx = 2

    sel = st.selectbox("", options, index=idx, key=key, disabled=(disabled or (not ready)))
    if (not ready) or sel == "-":
        return None
    return sel


# -----------------------------
# Round robin
# -----------------------------
def round_robin_pairs(teams):
    pairs = []
    n = len(teams)
    for i in range(n):
        for j in range(i + 1, n):
            pairs.append((teams[i], teams[j]))
    return pairs


def initial_state_from_config(cfg):
    yeargroups = []
    for yg in cfg["yeargroups"]:
        yname = yg["name"]
        teams = [team_id(yname, c) for c in yg["classes"]]
        matches = [{"home": a, "away": b, "result": None} for a, b in round_robin_pairs(teams)]
        yeargroups.append({"name": yname, "teams": teams, "group_matches": matches})

    return {
        "config": cfg,
        "yeargroups": yeargroups,
        "phase": "groups",  # groups | ko
        "qualified": {"main": [], "side": []},
        "brackets": {"main": None, "side": None},
        "tiebreaks": {},  # {yeargroup_name: {"teams":[...], "order":[...]}}
    }


# -----------------------------
# Ranking + Stechen logic
# -----------------------------
def compute_points(cfg, yg):
    pts_win = cfg["points"]["win"]
    pts_draw = cfg["points"]["draw"]
    pts_loss = cfg["points"]["loss"]

    points = {t: 0 for t in yg["teams"]}
    h2h = {t: {} for t in yg["teams"]}

    for m in yg["group_matches"]:
        h, a, r = m["home"], m["away"], m["result"]
        if r is None:
            continue

        h2h[h].setdefault(a, 0)
        h2h[a].setdefault(h, 0)

        if r == "H":
            points[h] += pts_win
            points[a] += pts_loss
            h2h[h][a] += pts_win
            h2h[a][h] += pts_loss
        elif r == "A":
            points[a] += pts_win
            points[h] += pts_loss
            h2h[a][h] += pts_win
            h2h[h][a] += pts_loss
        elif r == "D":
            points[h] += pts_draw
            points[a] += pts_draw
            h2h[h][a] += pts_draw
            h2h[a][h] += pts_draw

    return points, h2h


def base_order(cfg, yg):
    points, h2h = compute_points(cfg, yg)
    teams = list(yg["teams"])
    teams.sort(key=lambda t: points[t], reverse=True)

    i = 0
    while i < len(teams):
        j = i
        while j < len(teams) and points[teams[j]] == points[teams[i]]:
            j += 1
        tied = teams[i:j]
        if len(tied) > 1:
            def h2h_sum(t):
                return sum(h2h[t].get(o, 0) for o in tied if o != t)
            tied.sort(key=lambda t: (points[t], h2h_sum(t)), reverse=True)
            teams[i:j] = tied
        i = j

    return teams, points


def apply_tiebreak(state, yg_name, ordered):
    tb = state.get("tiebreaks", {}).get(yg_name)
    if not tb:
        return ordered

    teams = tb.get("teams", [])
    order = tb.get("order", [])
    if not teams or not order:
        return ordered
    if set(teams) != set(order):
        return ordered

    teams_set = set(teams)
    out = []
    it = iter(order)

    for t in ordered:
        out.append(None if t in teams_set else t)

    filled = []
    for x in out:
        filled.append(next(it) if x is None else x)

    return filled


def compute_table_with_tiebreak(state, yg):
    ordered, points = base_order(state["config"], yg)
    ordered = apply_tiebreak(state, yg["name"], ordered)
    return [{"Ranking": i + 1, "Team": t, "Punkten": points[t]} for i, t in enumerate(ordered)]


def group_progress(yg):
    total = len(yg["group_matches"])
    done = sum(1 for m in yg["group_matches"] if m["result"] is not None)
    return done, total


def overall_group_progress(state):
    done = 0
    total = 0
    for yg in state["yeargroups"]:
        d, t = group_progress(yg)
        done += d
        total += t
    return done, total


def all_groups_done(state):
    return all(group_progress(yg)[0] == group_progress(yg)[1] for yg in state["yeargroups"])


def boundary_index_for_yeargroup(yg):
    n = len(yg["teams"])
    if n == 4:
        return 1  # Grenze zwischen Platz2/3
    if n == 3:
        return 0  # Grenze zwischen Platz1/2
    return max(0, (n // 2) - 1)


def find_boundary_tie(state, yg):
    ordered, points = base_order(state["config"], yg)
    b = boundary_index_for_yeargroup(yg)
    if b + 1 >= len(ordered):
        return []

    boundary_pts = points[ordered[b]]
    tied = [t for t in ordered if points[t] == boundary_pts]
    pos = {t: ordered.index(t) for t in tied}

    # tie "schneidet" die Quali-Grenze
    if any(pos[t] <= b for t in tied) and any(pos[t] > b for t in tied):
        return tied
    return []


def qualification_lists(state):
    main, side = [], []
    for yg in state["yeargroups"]:
        table = compute_table_with_tiebreak(state, yg)
        ordered = [r["Team"] for r in table]
        n = len(yg["teams"])
        if n == 4:
            main += ordered[:2]
            side += ordered[2:]
        elif n == 3:
            main += ordered[:1]
            side += ordered[1:]
    return main, side


def missing_tiebreaks(state):
    miss = []
    for yg in state["yeargroups"]:
        tied = find_boundary_tie(state, yg)
        if tied:
            tb = state.get("tiebreaks", {}).get(yg["name"])
            if not tb:
                miss.append(yg["name"])
    return miss


# -----------------------------
# Brackets
# -----------------------------
def make_main_bracket(teams8):
    rnd = random.Random()
    shuffled = teams8[:]
    rnd.shuffle(shuffled)
    qf = [{"p1": shuffled[i], "p2": shuffled[i + 1], "winner": None} for i in range(0, 8, 2)]
    return {
        "rounds": [
            {"name": "Véirelsfinall", "matches": qf},
            {"name": "Halleffinall", "matches": [{"p1": None, "p2": None, "winner": None} for _ in range(2)]},
            {"name": "Finall", "matches": [{"p1": None, "p2": None, "winner": None}]},
        ]
    }


def make_side_bracket(teams10):
    rnd = random.Random()
    t = teams10[:]
    rnd.shuffle(t)

    r1_play = t[:6]
    byes = t[6:]

    r1 = [{"p1": r1_play[i], "p2": r1_play[i + 1], "winner": None} for i in range(0, 6, 2)]
    wildcard_match_index = rnd.randrange(0, 3)

    return {
        "meta": {"byes_round1": byes, "wildcard_match_index": wildcard_match_index},
        "rounds": [
            {"name": "1. Ronn", "matches": r1},
            {"name": "2. Ronn", "matches": [{"p1": None, "p2": None, "winner": None} for _ in range(3)], "bye": None},
            {"name": "Halleffinall", "matches": [{"p1": None, "p2": None, "winner": None} for _ in range(2)]},
            {"name": "Finall", "matches": [{"p1": None, "p2": None, "winner": None}]},
        ],
    }


def advance_main(bracket):
    r = bracket["rounds"]
    qf_winners = [m["winner"] for m in r[0]["matches"]]
    if all(qf_winners):
        r[1]["matches"][0]["p1"], r[1]["matches"][0]["p2"] = qf_winners[0], qf_winners[1]
        r[1]["matches"][1]["p1"], r[1]["matches"][1]["p2"] = qf_winners[2], qf_winners[3]
    sf_winners = [m["winner"] for m in r[1]["matches"]]
    if all(sf_winners):
        r[2]["matches"][0]["p1"], r[2]["matches"][0]["p2"] = sf_winners[0], sf_winners[1]


def advance_side(bracket):
    r = bracket["rounds"]
    meta = bracket["meta"]

    r1_winners = [m["winner"] for m in r[0]["matches"]]
    if all(r1_winners):
        wc_idx = meta["wildcard_match_index"]
        wildcard_team = r[0]["matches"][wc_idx]["winner"]

        r2_pool = meta["byes_round1"] + [w for w in r1_winners if w != wildcard_team]
        if len(r2_pool) == 6:
            for i in range(3):
                r[1]["matches"][i]["p1"] = r2_pool[2 * i]
                r[1]["matches"][i]["p2"] = r2_pool[2 * i + 1]
            r[1]["bye"] = wildcard_team

    r2_winners = [m["winner"] for m in r[1]["matches"]]
    if all(r2_winners) and r[1].get("bye"):
        sf = r2_winners + [r[1]["bye"]]
        r[2]["matches"][0]["p1"], r[2]["matches"][0]["p2"] = sf[0], sf[1]
        r[2]["matches"][1]["p1"], r[2]["matches"][1]["p2"] = sf[2], sf[3]

    sf_winners = [m["winner"] for m in r[2]["matches"]]
    if all(sf_winners):
        r[3]["matches"][0]["p1"], r[3]["matches"][0]["p2"] = sf_winners[0], sf_winners[1]


# -----------------------------
# Beamer rendering (kompakt)
# -----------------------------
def _team_html(name, winner):
    if not name:
        return "—"
    if winner and name == winner:
        return f"🏅 {name}"
    return name


def render_beamer_bracket(bracket, title, field_times, round_times, is_side=False):
    st.markdown(f"## {title}")
    st.markdown(f"<div class='timebar'>🕒 {field_times}</div>", unsafe_allow_html=True)

    rounds = bracket["rounds"]
    cols = st.columns(len(rounds), gap="small")

    meta = bracket.get("meta", {}) if is_side else {}
    byes_round1 = meta.get("byes_round1", [])
    wc_idx = meta.get("wildcard_match_index", None)

    for ri, rnd in enumerate(rounds):
        with cols[ri]:
            rt = round_times[ri] if ri < len(round_times) else ""
            st.markdown(f"### {rnd['name']}")
            if rt:
                st.markdown(f"<div class='roundtime'>🕒 {rt}</div>", unsafe_allow_html=True)

            if is_side and ri == 1 and rnd.get("bye"):
                st.markdown(
                    f"<div class='chip'>🎟️ Freilos (R2): <b>{rnd['bye']}</b></div>",
                    unsafe_allow_html=True,
                )

            for mi, m in enumerate(rnd["matches"]):
                p1, p2, w = m.get("p1"), m.get("p2"), m.get("winner")

                extra = ""
                if is_side and ri == 0 and wc_idx is not None and mi == wc_idx:
                    extra = "<div class='badge'>🎟️ Wildcard</div>"

                st.markdown(
                    f"""
                    <div class="match">
                      {extra}
                      <div class="t">{_team_html(p1, w)}</div>
                      <div class="vs">vs</div>
                      <div class="t">{_team_html(p2, w)}</div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

            if is_side and ri == 0 and byes_round1:
                # super-kompakt, damit es nicht nach unten drückt
                items = " • ".join(byes_round1)
                st.markdown(
                    f"<div class='byebox'><b>Freilos R1:</b> {items}</div>",
                    unsafe_allow_html=True,
                )


# -----------------------------
# App start
# -----------------------------
st.set_page_config(page_title="Schoulfest 2026", layout="wide")

state = load_state()
if state is None:
    state = initial_state_from_config(CONFIG)
    save_state(state)

view = st.query_params.get("view", "home")

if "beamer_authed" not in st.session_state:
    st.session_state["beamer_authed"] = False


# -----------------------------
# Sidebar (nur normal)
# -----------------------------
if view != "beamer":
    with st.sidebar:
        st.markdown("## Admin")

        reset_code = st.text_input("Reset-Code", type="password", placeholder="****", key="reset_code")
        if st.button("🔁 Reset (nur mit Code)"):
            if reset_code != RESET_CODE:
                st.error("Falsche Reset-Code")
            else:
                if os.path.exists(STATE_FILE):
                    os.remove(STATE_FILE)
                state = initial_state_from_config(CONFIG)
                save_state(state)
                st.success("Zurückgesetzt")
                st.rerun()

        st.divider()
        st.markdown("## Beamer")
        beamer_pw = st.text_input("Beamer-Passwuert", type="password", placeholder="****", key="beamer_pw_sidebar")
        if st.button("Beamer-View (neuer Tab)"):
            if beamer_pw == BEAMER_CODE:
                st.session_state["beamer_authed"] = True
                st.success("OK – Link klicken:")
                st.markdown(
                    '<a href="?view=beamer" target="_blank" style="font-size:18px; font-weight:800;">klick fir Beamer-View</a>',
                    unsafe_allow_html=True,
                )
            else:
                st.error("Falsche Beamer-Code")


# -----------------------------
# Beamer View (passt auf 1 Screen)
# -----------------------------
if view == "beamer":
    st.markdown(
        """
        <style>
          #MainMenu {visibility: hidden;}
          header {visibility: hidden;}
          footer {visibility: hidden;}
          [data-testid="stToolbar"] {visibility: hidden;}
          [data-testid="stSidebar"] {display: none;}

          .block-container{
            padding-top: .6rem !important;
            padding-bottom: .6rem !important;
            padding-left: 1.8rem !important;
            padding-right: 1.8rem !important;
            max-width: 2000px !important;
          }

          h1{font-size:44px !important; margin:0 0 .2rem 0 !important;}
          h2{font-size:26px !important; margin:.1rem 0 .1rem 0 !important;}
          h3{font-size:18px !important; margin:.1rem 0 .1rem 0 !important;}

          .timebar{font-size:14px; opacity:.85; margin-bottom:6px;}
          .roundtime{font-size:12px; opacity:.75; margin-top:-6px; margin-bottom:4px;}

          .match{
            border:1px solid rgba(255,255,255,0.16);
            border-radius:12px;
            padding:8px 10px;
            margin:6px 0;
            background:rgba(255,255,255,0.03);
            position:relative;
          }
          .t{font-size:16px; font-weight:800; line-height:1.2;}
          .vs{font-size:11px; opacity:.65; margin:3px 0;}

          .badge{
            position:absolute;
            top:6px;
            right:8px;
            font-size:11px;
            border:1px solid rgba(255,255,255,0.22);
            border-radius:999px;
            padding:2px 6px;
            opacity:.9;
            background:rgba(255,255,255,0.04);
          }

          .chip{
            font-size:12px;
            border:1px solid rgba(255,255,255,0.20);
            border-radius:999px;
            padding:4px 8px;
            background:rgba(255,255,255,0.04);
            display:inline-block;
            margin:4px 0 6px 0;
          }

          .byebox{
            margin-top:6px;
            font-size:12px;
            opacity:.9;
            border:1px dashed rgba(255,255,255,0.22);
            border-radius:12px;
            padding:6px 10px;
            background:rgba(255,255,255,0.02);
          }

          .split{
            height:1px;
            background:rgba(255,255,255,0.14);
            margin:8px 0;
          }
        </style>
        """,
        unsafe_allow_html=True,
    )

    if not st.session_state["beamer_authed"]:
        st.markdown("# 🔒 Beamer gespaart")
        pw = st.text_input("Passwort", type="password", placeholder="****", key="beamer_pw_page")
        if st.button("Fräischalten"):
            if pw == BEAMER_CODE:
                st.session_state["beamer_authed"] = True
                st.rerun()
            else:
                st.error("Falsche Code")
        st.stop()

    # live laden
    state_live = load_state() or state

    st.markdown("# Schoulfest 2026")

    if state_live.get("phase") == "ko" and state_live.get("brackets", {}).get("main") and state_live.get("brackets", {}).get("side"):
        main_b = copy.deepcopy(state_live["brackets"]["main"])
        side_b = copy.deepcopy(state_live["brackets"]["side"])

        advance_main(main_b)
        advance_side(side_b)

        field_times = ""

        render_beamer_bracket(main_b, "Hauptfeld", field_times, ["11:40", "13:40", "14:10"], is_side=False)
        st.markdown("<div class='split'></div>", unsafe_allow_html=True)
        render_beamer_bracket(side_b, "Nebenfeld", field_times, ["11:40", "13:10", "13:40", "14:10"], is_side=True)
    else:
        st.markdown("## Gruppen – Live Ranking")
        cols = st.columns(3)
        for i, yg in enumerate(state_live["yeargroups"]):
            with cols[i % 3]:
                st.markdown(f"### {yg['name']}")
                df = pd.DataFrame(compute_table_with_tiebreak(state_live, yg))
                st.dataframe(df, use_container_width=True, hide_index=True)

    time.sleep(BEAMER_REFRESH_SECONDS)
    st.rerun()


# -----------------------------
# Normal View
# -----------------------------
st.title("Schoulfest 2026")
mode = st.radio("Welche Ansicht?", ["Gruppephase", "KO-Phase"], horizontal=True)

if mode == "Gruppephase":
    st.subheader("Gruppephase")

    d_all, t_all = overall_group_progress(state)
    st.write(f"**Gesamtfortschritt:** {d_all}/{t_all}")
    st.progress((d_all / t_all) if t_all else 0)

    locked = (state["phase"] == "ko")
    if locked:
        st.warning("Gruppephase ist gesperrt, weil K.O. bereits gelost wurde.")

    yg_names = [yg["name"] for yg in state["yeargroups"]]
    yg_name = st.selectbox("Welche Gruppe/Jahrgang?", yg_names)
    yg_idx = yg_names.index(yg_name)
    yg = state["yeargroups"][yg_idx]

    d, t = group_progress(yg)
    st.write(f"**Fortschritt {yg_name}:** {d}/{t}")
    st.progress((d / t) if t else 0)

    left, right = st.columns([1.2, 0.8])

    with right:
        st.markdown("### Ranking")
        df = pd.DataFrame(compute_table_with_tiebreak(state, yg))
        st.dataframe(df, use_container_width=True, hide_index=True)

    with left:
        st.markdown("### Matcher")
        for mi, m in enumerate(yg["group_matches"]):
            c1, c2 = st.columns([0.7, 0.3])
            with c1:
                st.write(f"**Matcher {mi+1}:** {m['home']} vs {m['away']}")
            with c2:
                new_res = group_selectbox(
                    f"g_{yg_idx}_{mi}",
                    m["home"],
                    m["away"],
                    m["result"],
                    disabled=locked,
                )
                if (not locked) and new_res != m["result"]:
                    state["yeargroups"][yg_idx]["group_matches"][mi]["result"] = new_res
                    save_state(state)
                    st.rerun()

    # ✅ Stechen UI wieder drin + Tabelle updated nach Save (durch rerun)
    if (not locked) and (group_progress(yg)[0] == group_progress(yg)[1]):
        tied = find_boundary_tie(state, yg)
        if tied:
            st.warning(
                "Gläichstand un der Quali grenz - D'Teams mat Gläichstand ranken!:"
            )

            tb_saved = state.get("tiebreaks", {}).get(yg["name"], {})
            saved_order = tb_saved.get("order", [])

            order = []
            remaining = tied[:]
            for i in range(len(tied)):
                default_idx = 0
                if saved_order and i < len(saved_order) and saved_order[i] in remaining:
                    default_idx = remaining.index(saved_order[i])

                pick = st.selectbox(
                    f"Stiechen Rang {i+1}",
                    remaining,
                    index=default_idx,
                    key=f"tb_{yg['name']}_{i}",
                )
                order.append(pick)
                remaining = [x for x in remaining if x != pick]

            if st.button("✅ Stiechen späicheren", key=f"tb_save_{yg['name']}"):
                state.setdefault("tiebreaks", {})
                state["tiebreaks"][yg["name"]] = {"teams": tied, "order": order}
                save_state(state)
                st.success("Stiechen gespäicheren. Ranking gouf aktualiséiert.")
                st.rerun()

    st.divider()

    if not locked:
        if all_groups_done(state):
            miss = missing_tiebreaks(state)
            if miss:
                st.error("Stiechen fehlt fir: " + ", ".join(miss))
            else:
                st.success("All Gruppe fäerdeg → K.O. ka geloust ginn")
                draw_code = st.text_input("Auslousungs-Code", type="password", placeholder="****", key="draw_code")
                if st.button("➡️ K.O.-Phase lousen"):
                    if draw_code != DRAW_CODE:
                        st.error("Falschen Auslousungs-Code.")
                        st.stop()

                    main, side = qualification_lists(state)
                    if len(main) != 8 or len(side) != 10:
                        st.error(f"Teams stimmen nicht: Haupt={len(main)} (soll 8), Neben={len(side)} (soll 10).")
                        st.stop()

                    state["qualified"]["main"] = main
                    state["qualified"]["side"] = side
                    state["brackets"]["main"] = make_main_bracket(main)
                    state["brackets"]["side"] = make_side_bracket(side)
                    state["phase"] = "ko"
                    save_state(state)
                    st.success("K.O. erstallt a Gruppephase gespaart")
                    st.rerun()
        else:
            st.info("gëtt méiglech sou bal all Gruppephasen agedroe sinn")

else:
    st.subheader("KO-Phase")

    if state["phase"] != "ko":
        st.warning("K.O. ass nach net erstallt Gruppephase fäerdeg maachen!")
        st.stop()

    field = st.radio("Welches Feld?", ["Haaptfeld", "Niewenfeld"], horizontal=True)

    if field == "Haaptfeld":
        bracket = state["brackets"]["main"]
        advance_main(bracket)

        st.markdown("### Haaptfeld")
        cols = st.columns(len(bracket["rounds"]))
        for ri, rnd in enumerate(bracket["rounds"]):
            with cols[ri]:
                st.markdown(f"### {rnd['name']}")
                for mi, m in enumerate(rnd["matches"]):
                    with st.container(border=True):
                        st.caption(f"Match {mi+1}")
                        st.write(f"**{m.get('p1') or '-'}** vs **{m.get('p2') or '-'}**")
                        new_w = ko_selectbox(
                            f"main_{ri}_{mi}",
                            m.get("p1"),
                            m.get("p2"),
                            m.get("winner"),
                        )
                        if new_w != m.get("winner"):
                            m["winner"] = new_w
                            save_state(state)
                            st.rerun()

        champ = bracket["rounds"][-1]["matches"][0]["winner"]
        if champ:
            st.success(f"🏆 Gewënner Haaptfeld: **{champ}**")

    else:
        bracket = state["brackets"]["side"]
        advance_side(bracket)

        st.markdown("### Niewenfeld")

        meta = bracket.get("meta", {})
        byes_round1 = meta.get("byes_round1", [])
        wildcard_match_index = meta.get("wildcard_match_index", None)

        if byes_round1:
            st.info("✅ **Bye an der 1. Ronn:**\n\n- " + "\n- ".join(byes_round1))

        cols = st.columns(len(bracket["rounds"]))
        for ri, rnd in enumerate(bracket["rounds"]):
            with cols[ri]:
                st.markdown(f"### {rnd['name']}")
                if ri == 1 and rnd.get("bye"):
                    st.caption(f"🎟️ Wildcard-Freilous: {rnd['bye']}")

                for mi, m in enumerate(rnd["matches"]):
                    with st.container(border=True):
                        if ri == 0 and wildcard_match_index is not None and mi == wildcard_match_index:
                            st.markdown("**🎟️ Wildcard-Match** (Gewinner huet Bye an der 2. Ronn)")
                        st.caption(f"Match {mi+1}")
                        st.write(f"**{m.get('p1') or '-'}** vs **{m.get('p2') or '-'}**")
                        new_w = ko_selectbox(
                            f"side_{ri}_{mi}",
                            m.get("p1"),
                            m.get("p2"),
                            m.get("winner"),
                        )
                        if new_w != m.get("winner"):
                            m["winner"] = new_w
                            save_state(state)
                            st.rerun()

        champ = bracket["rounds"][-1]["matches"][0]["winner"]
        if champ:
            st.success(f"🏆 Gewënner Niewenfeld: **{champ}**")