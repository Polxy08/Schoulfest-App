import json
import os
import random
import streamlit as st

STATE_FILE = "tournament_state.json"
RESET_CODE = "2611"
DRAW_CODE = "0987"

CONFIG = {
    "yeargroups": [
        {"name": "7e", "classes": ["C1", "C2", "C3", "G"]},
        {"name": "6e", "classes": ["C1", "C2", "C3", "G"]},
        {"name": "5e", "classes": ["C1", "C3", "C3", "G"]},
        {"name": "4e", "classes": ["BC", "D", "G"]},
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
        "tiebreaks": {},  # {"Jahrgang X": {"teams":[...], "order":[...]}}
    }


# -----------------------------
# Ranking
# -----------------------------
def compute_table(cfg, yg):
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

    teams = list(yg["teams"])
    teams.sort(key=lambda t: points[t], reverse=True)

    # tie-break: head-to-head sum innerhalb gleicher Punkte
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

    return [{"#": k + 1, "Team": t, "Punkte": points[t]} for k, t in enumerate(teams)]


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


# -----------------------------
# Stechen / Tiebreak helpers
# -----------------------------
def points_map(cfg, yg):
    pts_win = cfg["points"]["win"]
    pts_draw = cfg["points"]["draw"]
    pts_loss = cfg["points"]["loss"]

    points = {t: 0 for t in yg["teams"]}
    for m in yg["group_matches"]:
        h, a, r = m["home"], m["away"], m["result"]
        if r is None:
            continue
        if r == "H":
            points[h] += pts_win
            points[a] += pts_loss
        elif r == "A":
            points[a] += pts_win
            points[h] += pts_loss
        elif r == "D":
            points[h] += pts_draw
            points[a] += pts_draw
    return points


def boundary_index_for_yeargroup(yg):
    n = len(yg["teams"])
    if n == 4:
        return 1  # Cut zwischen Platz 2 und 3
    if n == 3:
        return 0  # Cut zwischen Platz 1 und 2
    return max(0, (n // 2) - 1)


def find_boundary_tie(cfg, yg):
    table = compute_table(cfg, yg)
    ordered = [r["Team"] for r in table]
    pts = points_map(cfg, yg)

    b = boundary_index_for_yeargroup(yg)
    if b + 1 >= len(ordered):
        return []

    boundary_pts = pts[ordered[b]]
    tied = [t for t in ordered if pts[t] == boundary_pts]

    pos = {t: ordered.index(t) for t in tied}
    if any(pos[t] <= b for t in tied) and any(pos[t] > b for t in tied):
        return tied
    return []


def apply_tiebreak_if_needed(state, yg, ordered):
    name = yg["name"]
    tb = state.get("tiebreaks", {}).get(name)
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
    order_iter = iter(order)

    for t in ordered:
        if t in teams_set:
            out.append(None)
        else:
            out.append(t)

    filled = []
    for x in out:
        if x is None:
            filled.append(next(order_iter))
        else:
            filled.append(x)

    return filled


def qualification_lists_with_tiebreaks(state):
    main, side = [], []
    for yg in state["yeargroups"]:
        table = compute_table(state["config"], yg)
        ordered = [r["Team"] for r in table]
        ordered = apply_tiebreak_if_needed(state, yg, ordered)

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
        tied = find_boundary_tie(state["config"], yg)
        if tied and not state.get("tiebreaks", {}).get(yg["name"]):
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
            {"name": "Viertelfinale", "matches": qf},
            {"name": "Halbfinale", "matches": [{"p1": None, "p2": None, "winner": None} for _ in range(2)]},
            {"name": "Finale", "matches": [{"p1": None, "p2": None, "winner": None}]},
        ]
    }


def make_side_bracket(teams10):
    rnd = random.Random()
    t = teams10[:]
    rnd.shuffle(t)
    r1_play = t[:6]
    byes = t[6:]
    r1 = [{"p1": r1_play[i], "p2": r1_play[i + 1], "winner": None} for i in range(0, 6, 2)]
    return {
        "meta": {"byes_round2": byes, "wildcard_from_r1": None},
        "rounds": [
            {"name": "Runde 1", "matches": r1},
            {"name": "Runde 2", "matches": [{"p1": None, "p2": None, "winner": None} for _ in range(3)], "bye": None},
            {"name": "Halbfinale", "matches": [{"p1": None, "p2": None, "winner": None} for _ in range(2)]},
            {"name": "Finale", "matches": [{"p1": None, "p2": None, "winner": None}]},
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
        if meta["wildcard_from_r1"] is None:
            meta["wildcard_from_r1"] = random.choice(r1_winners)

        wc = meta["wildcard_from_r1"]
        r2_pool = meta["byes_round2"] + [w for w in r1_winners if w != wc]
        if len(r2_pool) == 6:
            for i in range(3):
                r[1]["matches"][i]["p1"] = r2_pool[2 * i]
                r[1]["matches"][i]["p2"] = r2_pool[2 * i + 1]
            r[1]["bye"] = wc

    r2_winners = [m["winner"] for m in r[1]["matches"]]
    if all(r2_winners) and r[1].get("bye"):
        sf = r2_winners + [r[1]["bye"]]
        r[2]["matches"][0]["p1"], r[2]["matches"][0]["p2"] = sf[0], sf[1]
        r[2]["matches"][1]["p1"], r[2]["matches"][1]["p2"] = sf[2], sf[3]

    sf_winners = [m["winner"] for m in r[2]["matches"]]
    if all(sf_winners):
        r[3]["matches"][0]["p1"], r[3]["matches"][0]["p2"] = sf_winners[0], sf_winners[1]


def ko_progress(bracket):
    total = 0
    done = 0
    for rnd in bracket["rounds"]:
        for m in rnd["matches"]:
            if m.get("p1") and m.get("p2"):
                total += 1
                if m.get("winner"):
                    done += 1
    return done, total


# -----------------------------
# UI helpers
# -----------------------------
def group_selectbox(key, home, away, current):
    options = ["-", f"{home} gewinnt", "Unentschieden", f"{away} gewinnt"]
    mapping = {None: 0, "H": 1, "D": 2, "A": 3}
    inv = {0: None, 1: "H", 2: "D", 3: "A"}
    idx = mapping.get(current, 0)
    sel = st.selectbox("", options, index=idx, key=key, disabled=(state["phase"] == "ko"))
    return inv[options.index(sel)]


def ko_selectbox(key, p1, p2, current):
    ready = bool(p1 and p2)
    options = ["-", p1 or "-", p2 or "-"]
    idx = 0
    if ready and current == p1:
        idx = 1
    elif ready and current == p2:
        idx = 2
    sel = st.selectbox("", options, index=idx, key=key, disabled=(not ready))
    if (not ready) or sel == "-":
        return None
    return sel


def render_bracket_tree(bracket, key_prefix, show_info_extra=False):
    done, total = ko_progress(bracket)
    if total > 0:
        st.write(f"**Fortschritt:** {done}/{total}")
        st.progress(done / total)
    else:
        st.info("Noch keine Matches bereit (Teams fehlen).")

    cols = st.columns(len(bracket["rounds"]))
    for ri, rnd in enumerate(bracket["rounds"]):
        with cols[ri]:
            st.markdown(f"### {rnd['name']}")
            if show_info_extra and rnd.get("bye"):
                st.caption(f"🎟️ Freilos: {rnd['bye']}")

            for mi, m in enumerate(rnd["matches"]):
                p1, p2 = m.get("p1"), m.get("p2")
                with st.container(border=True):
                    st.caption(f"Match {mi+1}")
                    st.write(f"**{p1 or '-'}** vs **{p2 or '-'}**")
                    new_w = ko_selectbox(f"{key_prefix}_{ri}_{mi}", p1, p2, m.get("winner"))
                    if new_w != m.get("winner"):
                        m["winner"] = new_w
                        save_state(state)
                        st.rerun()


# -----------------------------
# App
# -----------------------------
st.set_page_config(page_title="Schoulfest", layout="wide")
st.title("Schoulfest")

state = load_state()
if state is None:
    state = initial_state_from_config(CONFIG)
    save_state(state)

with st.sidebar:
    st.markdown("## Admin")

    reset_code = st.text_input("Reset-Code", type="password", placeholder="****", key="reset_code")
    if st.button("🔁 Reset (nur mit Code)"):
        if reset_code != RESET_CODE:
            st.error("Falscher Reset-Code.")
        else:
            if os.path.exists(STATE_FILE):
                os.remove(STATE_FILE)
            state = initial_state_from_config(CONFIG)
            save_state(state)
            st.success("Zurückgesetzt.")
            st.rerun()

st.divider()

st.header("Start")
mode = st.radio("Welche Runde willst du bearbeiten?", ["Gruppenphase", "KO-Phase"], horizontal=True)

# -----------------------------
# Gruppenphase
# -----------------------------
if mode == "Gruppenphase":
    st.subheader("Gruppenphase")

    d_all, t_all = overall_group_progress(state)
    st.write(f"**Gesamtfortschritt:** {d_all}/{t_all}")
    st.progress((d_all / t_all) if t_all else 0)

    if state["phase"] == "ko":
        st.warning("Gruppenphase ist gesperrt, weil K.O. bereits gelost wurde.")

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
        st.table(compute_table(state["config"], yg))

    with left:
        st.markdown("### Spiele")
        for mi, m in enumerate(yg["group_matches"]):
            c1, c2 = st.columns([0.7, 0.3])
            with c1:
                st.write(f"**Spiel {mi+1}:** {m['home']} vs {m['away']}")
            with c2:
                new_res = group_selectbox(f"g_{yg_idx}_{mi}", m["home"], m["away"], m["result"])
                if state["phase"] != "ko" and new_res != m["result"]:
                    state["yeargroups"][yg_idx]["group_matches"][mi]["result"] = new_res
                    save_state(state)
                    st.rerun()

    st.divider()

    # --- Stechen UI (nur bei Gleichstand an der Quali-Grenze) ---
    if state["phase"] != "ko" and all_groups_done(state):
        tied = find_boundary_tie(state["config"], yg)
        if tied:
            st.warning("⚠️ Gleichstand an der Quali-Grenze! Bitte Stechen eintragen (nur diese Teams).")

            tb_saved = state.get("tiebreaks", {}).get(yg["name"], {})
            saved_order = tb_saved.get("order", [])

            st.caption("Wähle die Reihenfolge nach dem Stechen. Nur diese Teams werden untereinander sortiert.")
            order = []
            remaining = tied[:]
            for i in range(len(tied)):
                default_idx = 0
                if saved_order and i < len(saved_order) and saved_order[i] in remaining:
                    default_idx = remaining.index(saved_order[i])

                pick = st.selectbox(
                    f"Stechen Rang {i+1}",
                    remaining,
                    index=default_idx,
                    key=f"tb_{yg['name']}_{i}",
                )
                order.append(pick)
                remaining = [x for x in remaining if x != pick]

            if st.button("✅ Stechen speichern", key=f"tb_save_{yg['name']}"):
                state.setdefault("tiebreaks", {})
                state["tiebreaks"][yg["name"]] = {"teams": tied, "order": order}
                save_state(state)
                st.success("Stechen gespeichert.")
                st.rerun()

    # --- KO erstellen (mit Passwort + blockiert wenn Stechen fehlt) ---
    if state["phase"] != "ko":
        if all_groups_done(state):
            miss = missing_tiebreaks(state)
            if miss:
                st.error("Stechen fehlt für: " + ", ".join(miss))
            else:
                st.success("Alle Gruppen fertig → K.O. kann gelost werden (nur mit Passwort).")
                draw_code = st.text_input("Auslosungs-Passwort", type="password", placeholder="****", key="draw_code")
                if st.button("➡️ K.O.-Phase losen/erstellen (Passwort nötig)"):
                    if draw_code != DRAW_CODE:
                        st.error("Falsches Auslosungs-Passwort.")
                        st.stop()

                    main, side = qualification_lists_with_tiebreaks(state)
                    if len(main) != 8 or len(side) != 10:
                        st.error(f"Teams stimmen nicht: Haupt={len(main)} (soll 8), Neben={len(side)} (soll 10).")
                        st.stop()

                    state["qualified"]["main"] = main
                    state["qualified"]["side"] = side
                    state["brackets"]["main"] = make_main_bracket(main)
                    state["brackets"]["side"] = make_side_bracket(side)
                    state["phase"] = "ko"
                    save_state(state)
                    st.success("K.O. erstellt und Gruppenphase gesperrt ✅")
                    st.rerun()
        else:
            st.info("K.O. wird erst möglich, wenn **alle** Gruppenspiele eingetragen sind.")
    else:
        st.caption("K.O. wurde bereits erstellt. Gruppenphase ist deshalb gesperrt.")

# -----------------------------
# KO-Phase
# -----------------------------
else:
    st.subheader("KO-Phase")

    if state["phase"] != "ko":
        st.warning("K.O. ist noch nicht erstellt (erst Gruppenphase fertig machen).")
        st.stop()

    field = st.radio("Welches Feld?", ["Hauptfeld", "Nebenfeld"], horizontal=True)

    if field == "Hauptfeld":
        bracket = state["brackets"]["main"]
        advance_main(bracket)
        st.markdown("### Hauptfeld – Baum")
        render_bracket_tree(bracket, key_prefix="main")
        champ = bracket["rounds"][-1]["matches"][0]["winner"]
        if champ:
            st.success(f"🏆 Sieger Hauptfeld: **{champ}**")

    else:
        bracket = state["brackets"]["side"]
        advance_side(bracket)
        st.markdown("### Nebenfeld – Baum")
        render_bracket_tree(bracket, key_prefix="side", show_info_extra=True)
        champ = bracket["rounds"][-1]["matches"][0]["winner"]
        if champ:
            st.success(f"🏆 Sieger Nebenfeld: **{champ}**")