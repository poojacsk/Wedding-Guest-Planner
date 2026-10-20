import json
import re
import urllib.parse
import uuid
from pathlib import Path

import pandas as pd
import streamlit as st

st.set_page_config(page_title="Wedding Planner", page_icon="💍", layout="wide")

DB = Path(__file__).with_name("wedding_data.json")
SIDES = ["Bride", "Groom"]
RSVP = ["Pending", "Confirmed", "Declined"]
EMPTY = {"guests": [], "hotels": [], "tasks": [], "budget": [], "title": "Our Wedding"}


# ---------- storage ----------
def load():
    if DB.exists():
        try:
            return {**EMPTY, **json.loads(DB.read_text(encoding="utf-8"))}
        except Exception:
            pass
    return json.loads(json.dumps(EMPTY))


def save():
    DB.write_text(json.dumps(st.session_state.data, ensure_ascii=False, indent=1), encoding="utf-8")


if "data" not in st.session_state:
    st.session_state.data = load()
D = st.session_state.data


def new_guest(name, side, headcount=1, phone="", relation="", rsvp="Pending"):
    return {"id": uuid.uuid4().hex[:8], "name": name.strip(), "side": side,
            "headcount": int(headcount), "phone": phone.strip(), "relation": relation.strip(),
            "rsvp": rsvp, "hotel": "", "room": "", "checkin": "", "checkout": ""}


# ---------- OCR (Tamil) ----------
TAMIL_DIGITS = str.maketrans("௦௧௨௩௪௫௬௭௮௯", "0123456789")


def run_ocr(file):
    try:
        import pytesseract
        from PIL import Image, ImageOps
    except ImportError:
        return None, "Install OCR support: pip install pytesseract pillow"
    try:
        img = ImageOps.autocontrast(ImageOps.grayscale(Image.open(file)))
        w, h = img.size
        if w < 1600:  # upscale small photos for better accuracy
            img = img.resize((w * 2, h * 2))
        return pytesseract.image_to_string(img, lang="tam+eng", config="--psm 6"), None
    except Exception as e:
        return None, f"OCR failed: {e}. Make sure Tesseract and the Tamil pack (tesseract-ocr-tam) are installed."


def parse_lines(text, side):
    out = []
    for line in text.translate(TAMIL_DIGITS).splitlines():
        line = re.sub(r"^\s*[\d]+[.)\-:]\s+", "", line).strip(" \t|•*-_")
        if len(line) < 2:
            continue
        m = re.match(r"^(.*?)[\s,:\-–=(]+(\d{1,2})\)?$", line)
        name, count = (m.group(1).strip(), int(m.group(2))) if m else (line, 1)
        if name:
            out.append(new_guest(name, side, max(count, 1)))
    return out


# ---------- helpers ----------
def wa_number(p):
    d = re.sub(r"\D", "", p or "")
    return ("91" + d) if len(d) == 10 else d


def room_message(g):
    h = next((x for x in D["hotels"] if x["name"] == g["hotel"]), None)
    if not h:
        return ""
    lines = [f"🎉 *{D['title']} – Accommodation Details*", "", f"Dear {g['name']},",
             "We're delighted to host you. Your stay details:", "",
             f"🏨 Hotel: {h['name']}", f"📍 Address: {h['address']}"]
    if h.get("link"):
        lines.append(f"🗺️ Location: {h['link']}")
    lines.append(f"🚪 Room: {g['room'] or 'To be confirmed'}")
    if g["checkin"]:
        lines.append(f"🕑 Check-in: {g['checkin']}")
    if g["checkout"]:
        lines.append(f"🕚 Check-out: {g['checkout']}")
    if h.get("contact"):
        lines.append(f"☎️ Hotel contact: {h['contact']}")
    lines += ["", "See you at the celebration! 💐"]
    return "\n".join(lines)


def guests_df(view):
    df = pd.DataFrame(D["guests"], columns=["id", "name", "side", "headcount", "phone", "relation",
                                            "rsvp", "hotel", "room", "checkin", "checkout"])
    return df if view == "All" else df[df.side == view]


def merge_edits(edited, cols):
    """Apply edited rows (by id) back to the master guest list."""
    by_id = {g["id"]: g for g in D["guests"]}
    for _, r in edited.iterrows():
        g = by_id.get(r["id"])
        if g is None:
            continue
        for c in cols:
            v = r[c]
            g[c] = int(v) if c == "headcount" and pd.notna(v) else ("" if pd.isna(v) else v)
    save()


# ---------- header ----------
st.title("💍 " + D["title"])
c1, c2 = st.columns([3, 2])
with c1:
    new_title = st.text_input("Wedding title (used in shared messages)", D["title"])
    if new_title != D["title"]:
        D["title"] = new_title
        save()
with c2:
    view = st.radio("View", ["All", "Bride", "Groom"], horizontal=True, key="view")

tabs = st.tabs(["📊 Dashboard", "👥 Guest list", "🏨 Rooms & sharing", "✅ Tasks", "💰 Budget"])

# ---------- Dashboard ----------
with tabs[0]:
    df = pd.DataFrame(D["guests"], columns=guests_df("All").columns)
    if df.empty:
        st.info("No guests yet. Add some in the Guest list tab.")
    else:
        cols = st.columns(3)
        for col, side in zip(cols, SIDES + ["Total"]):
            s = df if side == "Total" else df[df.side == side]
            col.metric(f"{side} – headcount", int(s.headcount.sum()), f"{len(s)} invitations")
        a, b = st.columns(2)
        with a:
            st.subheader("RSVP (headcount)")
            st.bar_chart(df.pivot_table(index="rsvp", columns="side", values="headcount", aggfunc="sum").fillna(0))
        with b:
            st.subheader("Accommodation")
            assigned = df[df.hotel != ""]
            st.metric("Guests with rooms", f"{len(assigned)} / {len(df)}")
            if not assigned.empty:
                st.bar_chart(assigned.groupby("hotel").headcount.sum())
    st.download_button("⬇️ Download full guest list (CSV)",
                       pd.DataFrame(D["guests"]).to_csv(index=False).encode("utf-8-sig"),
                       "guests.csv", "text/csv", disabled=not D["guests"])

# ---------- Guest list ----------
with tabs[1]:
    st.subheader("Add guests")
    m1, m2, m3 = st.tabs(["✍️ Add one", "📝 Paste / type list", "📷 Scan Tamil list"])

    with m1:
        with st.form("add_guest", clear_on_submit=True):
            a, b, c = st.columns(3)
            name = a.text_input("Name (Tamil or English)")
            side = b.selectbox("Side", SIDES, index=0 if view != "Groom" else 1)
            hc = c.number_input("Headcount", 1, 50, 1)
            d, e, f = st.columns(3)
            phone = d.text_input("Phone / WhatsApp")
            rel = e.text_input("Relation (e.g. Uncle, Friend)")
            rsvp = f.selectbox("RSVP", RSVP)
            if st.form_submit_button("➕ Add guest") and name.strip():
                D["guests"].append(new_guest(name, side, hc, phone, rel, rsvp))
                save()
                st.success(f"Added {name}")
                st.rerun()

    with m2:
        st.caption("One guest per line. Put headcount at the end, e.g. `முருகன் குடும்பம், 4`")
        txt = st.text_area("Guests", height=150, key="bulk_txt")
        bs = st.radio("Side", SIDES, horizontal=True, key="bulk_side")
        if st.button("Add all lines") and txt.strip():
            new = parse_lines(txt, bs)
            D["guests"] += new
            save()
            st.success(f"Added {len(new)} guests")
            st.session_state.pop("bulk_txt", None)
            st.rerun()

    with m3:
        st.caption("Upload photos of the handwritten/printed list. Review the text before adding "
                   "(handwriting accuracy varies, so always check).")
        ups = st.file_uploader("Images", type=["png", "jpg", "jpeg", "webp"], accept_multiple_files=True)
        os_side = st.radio("These names belong to", SIDES, horizontal=True, key="ocr_side")
        if ups and st.button("🔍 Read text"):
            texts = []
            for u in ups:
                t, err = run_ocr(u)
                if err:
                    st.error(err)
                    break
                texts.append(t)
            else:
                st.session_state.ocr_text = "\n".join(texts)
        if "ocr_text" in st.session_state:
            edited_txt = st.text_area("Extracted text (fix mistakes here)", st.session_state.ocr_text, height=200)
            prev = parse_lines(edited_txt, os_side)
            st.write(f"**{len(prev)} guests detected**")
            if prev:
                st.dataframe(pd.DataFrame(prev)[["name", "headcount", "side"]], hide_index=True)
            if st.button("✅ Add these guests") and prev:
                D["guests"] += prev
                save()
                del st.session_state["ocr_text"]
                st.rerun()

    st.divider()
    st.subheader(f"Guest list – {view}")
    q = st.text_input("🔎 Search", "")
    gdf = guests_df(view)
    if q:
        gdf = gdf[gdf.name.str.contains(q, case=False, na=False) | gdf.relation.str.contains(q, case=False, na=False)]
    gdf = gdf[["id", "name", "side", "headcount", "phone", "relation", "rsvp"]].copy()
    gdf["delete"] = False
    st.caption(f"Total headcount shown: **{int(gdf.headcount.sum())}** across {len(gdf)} invitations")
    ed = st.data_editor(
        gdf, hide_index=True, use_container_width=True, key=f"ge_{view}_{q}",
        column_config={"id": None, "side": st.column_config.SelectboxColumn("Side", options=SIDES),
                       "rsvp": st.column_config.SelectboxColumn("RSVP", options=RSVP),
                       "headcount": st.column_config.NumberColumn("Headcount", min_value=1, max_value=50),
                       "delete": st.column_config.CheckboxColumn("🗑️")})
    if st.button("💾 Save changes"):
        gone = set(ed[ed["delete"]]["id"])
        merge_edits(ed[~ed["id"].isin(gone)], ["name", "side", "headcount", "phone", "relation", "rsvp"])
        D["guests"] = [g for g in D["guests"] if g["id"] not in gone]
        save()
        st.rerun()

# ---------- Rooms & sharing ----------
with tabs[2]:
    st.subheader("1. Hotels")
    with st.form("add_hotel", clear_on_submit=True):
        a, b = st.columns(2)
        hn = a.text_input("Hotel name")
        hc_ = b.text_input("Hotel contact number")
        ha = st.text_area("Address", height=70)
        hl = st.text_input("Google Maps link")
        if st.form_submit_button("➕ Add hotel") and hn.strip():
            D["hotels"].append({"name": hn.strip(), "address": ha.strip(), "link": hl.strip(), "contact": hc_.strip()})
            save()
            st.rerun()
    if D["hotels"]:
        hdf = st.data_editor(pd.DataFrame(D["hotels"]), num_rows="dynamic", use_container_width=True,
                             hide_index=True, key="hotel_ed",
                             column_config={"link": st.column_config.TextColumn("Maps link")})
        if st.button("💾 Save hotels"):
            D["hotels"] = [r for r in hdf.fillna("").to_dict("records") if r["name"]]
            save()
            st.rerun()

    st.divider()
    st.subheader("2. Allot rooms")
    hotel_names = [""] + [h["name"] for h in D["hotels"]]
    rdf = guests_df(view)[["id", "name", "side", "headcount", "hotel", "room", "checkin", "checkout"]]
    only_un = st.checkbox("Show only guests without a room")
    if only_un:
        rdf = rdf[rdf.hotel == ""]
    red = st.data_editor(
        rdf, hide_index=True, use_container_width=True, key=f"re_{view}_{only_un}",
        disabled=["name", "side", "headcount"],
        column_config={"id": None, "hotel": st.column_config.SelectboxColumn("Hotel", options=hotel_names),
                       "room": "Room no.", "checkin": "Check-in (date/time)", "checkout": "Check-out"})
    if st.button("💾 Save room allotments"):
        merge_edits(red, ["hotel", "room", "checkin", "checkout"])
        st.success("Saved")
        st.rerun()

    st.divider()
    st.subheader("3. Share room details")
    ass = [g for g in D["guests"] if g["hotel"] and (view == "All" or g["side"] == view)]
    if not ass:
        st.info("Allot a hotel to a guest to enable sharing.")
    else:
        rows = []
        for g in ass:
            msg = room_message(g)
            rows.append({"Guest": g["name"], "Side": g["side"], "Hotel": g["hotel"], "Room": g["room"],
                         "WhatsApp": f"https://wa.me/{wa_number(g['phone'])}?text={urllib.parse.quote(msg)}"
                         if g["phone"] else None})
        st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True,
                     column_config={"WhatsApp": st.column_config.LinkColumn("Send", display_text="📲 WhatsApp")})
        pick = st.selectbox("Preview / copy message for", ass, format_func=lambda g: f"{g['name']} ({g['side']})")
        msg = room_message(pick)
        st.code(msg, language=None)  # copy button appears on hover
        st.link_button("📲 Share via WhatsApp (choose contact)",
                       f"https://wa.me/{wa_number(pick['phone'])}?text={urllib.parse.quote(msg)}"
                       if pick["phone"] else f"https://wa.me/?text={urllib.parse.quote(msg)}")
        st.caption("Guests without a phone number open WhatsApp so you can pick a contact.")

# ---------- Tasks ----------
with tabs[3]:
    st.subheader("Wedding checklist")
    if not D["tasks"]:
        D["tasks"] = [{"task": t, "owner": "", "due": "", "done": False} for t in
                      ["Book venue", "Book hotels for guests", "Finalise guest list", "Print & send invitations",
                       "Book catering", "Book photographer", "Arrange transport for guests"]]
    tdf = st.data_editor(pd.DataFrame(D["tasks"]), num_rows="dynamic", use_container_width=True, hide_index=True,
                         column_config={"done": st.column_config.CheckboxColumn("Done"), "due": "Due date"})
    if st.button("💾 Save tasks"):
        D["tasks"] = tdf.fillna("").to_dict("records")
        save()
    done = sum(bool(t["done"]) for t in D["tasks"])
    st.progress(done / max(len(D["tasks"]), 1), f"{done}/{len(D['tasks'])} done")

# ---------- Budget ----------
with tabs[4]:
    st.subheader("Budget tracker")
    if not D["budget"]:
        D["budget"] = [{"item": "Venue", "side": "Shared", "estimated": 0.0, "paid": 0.0}]
    bdf = st.data_editor(pd.DataFrame(D["budget"]), num_rows="dynamic", use_container_width=True, hide_index=True,
                         column_config={"side": st.column_config.SelectboxColumn("Side", options=SIDES + ["Shared"]),
                                        "estimated": st.column_config.NumberColumn("Estimated (₹)", min_value=0),
                                        "paid": st.column_config.NumberColumn("Paid (₹)", min_value=0)})
    if st.button("💾 Save budget"):
        D["budget"] = bdf.fillna("").to_dict("records")
        save()
    est, paid = pd.to_numeric(bdf.estimated, errors="coerce").sum(), pd.to_numeric(bdf.paid, errors="coerce").sum()
    x, y, z = st.columns(3)
    x.metric("Estimated", f"₹{est:,.0f}")
    y.metric("Paid", f"₹{paid:,.0f}")
    z.metric("Balance", f"₹{est - paid:,.0f}")
