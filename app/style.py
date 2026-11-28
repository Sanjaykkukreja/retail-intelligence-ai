"""Look and feel: one small design system for every page.

Calm and minimal: Inter type, a warm off-white page, white cards with hairline borders, one deep-green accent,
generous spacing. Colours live in one place (TOKENS) so a client can be given their own accent later.
"""
from __future__ import annotations

import html

import streamlit as st

TOKENS = dict(
    bg="#FAFAF8", surface="#FFFFFF", sidebar="#F2F1ED", text="#1A1A18", muted="#6B6A65", faint="#9A988F",
    border="#E6E4DE", accent="#1F6F5C", accent_soft="#E7F0EC", danger="#B42318", danger_soft="#FDECEA",
    warn="#B54708", warn_soft="#FEF3E2", ok="#1F6F5C", ok_soft="#E7F0EC",
)
CHART = ["#1F6F5C", "#9DB9AF", "#C98A4B", "#6B6A65"]

_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&display=swap');
html, body, [class*="css"], .stMarkdown, .stText, button, input, textarea, select { font-family: 'Inter', system-ui, sans-serif; }
.block-container { max-width: 1180px; padding-top: 2.2rem; padding-bottom: 4rem; }
h1, h2, h3 { letter-spacing: -0.02em; color: {text}; }
h1 { font-size: 1.7rem !important; font-weight: 600 !important; }
h2 { font-size: 1.25rem !important; font-weight: 600 !important; }
h3 { font-size: 1.05rem !important; font-weight: 600 !important; }
p, li { color: {text}; }
[data-testid="stCaptionContainer"], .stCaption { color: {muted} !important; }

/* sidebar */
[data-testid="stSidebar"] { background: {sidebar}; border-right: 1px solid {border}; }
[data-testid="stSidebarNav"] a span { font-size: 0.92rem; }
[data-testid="stSidebarNavSeparator"] { border-color: {border}; }

/* cards: metrics and bordered containers */
[data-testid="stMetric"] { background: {surface}; border: 1px solid {border}; border-radius: 12px; padding: 14px 16px; }
[data-testid="stMetricLabel"] p { color: {muted} !important; font-size: 0.8rem !important; }
[data-testid="stMetricValue"] { font-size: 1.45rem !important; font-weight: 600 !important; color: {text}; }
[data-testid="stVerticalBlockBorderWrapper"] { border-radius: 14px !important; border-color: {border} !important; }
div[data-testid="stVerticalBlockBorderWrapper"]:has(> div > [data-testid="stVerticalBlock"]) { background: {surface}; }
[data-testid="stExpander"] details { background: {surface}; border: 1px solid {border}; border-radius: 12px; }
[data-testid="stDataFrame"] { border: 1px solid {border}; border-radius: 10px; overflow: hidden; }

/* controls */
.stButton > button, .stDownloadButton > button { border-radius: 9px; border: 1px solid {border}; font-weight: 500; padding: 0.35rem 0.9rem; }
.stButton > button:hover { border-color: {accent}; color: {accent}; }
.stButton > button[kind="primary"] { background: {accent}; border-color: {accent}; color: #fff; }
[data-testid="stTabs"] button[role="tab"] p { font-size: 0.92rem; font-weight: 500; }
[data-testid="stChatInput"] textarea { font-size: 0.97rem; }
[data-testid="stChatMessage"] { background: transparent; }

/* our components */
.ri-eyebrow { font-size: 0.75rem; font-weight: 600; letter-spacing: 0.08em; text-transform: uppercase; color: {accent}; margin-bottom: 0.2rem; }
.ri-sub { color: {muted}; font-size: 0.98rem; margin: -0.4rem 0 1.4rem 0; max-width: 760px; }
.ri-pill { display: inline-block; padding: 2px 10px; border-radius: 999px; font-size: 0.76rem; font-weight: 500; margin-right: 6px;
           border: 1px solid {border}; background: {surface}; color: {muted}; }
.ri-pill.ok { background: {ok_soft}; color: {ok}; border-color: transparent; }
.ri-pill.warn { background: {warn_soft}; color: {warn}; border-color: transparent; }
.ri-pill.danger { background: {danger_soft}; color: {danger}; border-color: transparent; }
.ri-brand { font-weight: 600; font-size: 1.05rem; letter-spacing: -0.01em; color: {text}; }
.ri-brand span { color: {accent}; }
.ri-client { font-size: 0.82rem; color: {muted}; margin-top: 2px; }
.ri-card-title { font-weight: 600; font-size: 1rem; margin-bottom: 2px; }
.ri-card-sub { color: {muted}; font-size: 0.83rem; margin-bottom: 10px; }
.ri-hero { font-size: 1.6rem; font-weight: 600; letter-spacing: -0.02em; margin: 0.2rem 0 0.3rem 0; }
.ri-chat-empty { text-align: center; margin: 3.5rem 0 1.5rem 0; }
.ri-chat-empty .ri-hero { font-size: 1.9rem; }
footer, [data-testid="stDecoration"] { display: none; }
</style>
"""


def apply_style() -> None:
    css = _CSS
    for k, v in TOKENS.items():
        css = css.replace("{" + k + "}", v)
    st.markdown(css, unsafe_allow_html=True)


def esc(text) -> str:
    return html.escape(str(text))


def page_header(title: str, subtitle: str = "", eyebrow: str = "") -> None:
    if eyebrow:
        st.markdown(f'<div class="ri-eyebrow">{esc(eyebrow)}</div>', unsafe_allow_html=True)
    st.title(title)
    if subtitle:
        st.markdown(f'<div class="ri-sub">{esc(subtitle)}</div>', unsafe_allow_html=True)


def pills(*items: tuple[str, str]) -> None:
    """Small status labels: pills(("L1 recommend", ""), ("3 waiting", "warn"))."""
    st.markdown("".join(f'<span class="ri-pill {tone}">{esc(t)}</span>' for t, tone in items), unsafe_allow_html=True)


def card_title(title: str, sub: str = "") -> None:
    st.markdown(f'<div class="ri-card-title">{esc(title)}</div>' + (f'<div class="ri-card-sub">{esc(sub)}</div>' if sub else ""),
                unsafe_allow_html=True)


def severity_label(sev: str) -> str:
    """Coloured severity for expander labels (Streamlit markdown colours)."""
    return {"high": ":red[High]", "medium": ":orange[Medium]"}.get(str(sev).lower(), ":gray[Low]")


def sidebar_brand(client_name: str, note: str, logo: str, icon: str) -> None:
    """Wordmark above the menu (st.logo), and the client and data status below it."""
    st.logo(logo, size="large", icon_image=icon)
    st.sidebar.markdown(f'<div class="ri-client" style="margin-top:0.6rem"><b>{esc(client_name)}</b></div>',
                        unsafe_allow_html=True)
    st.sidebar.caption(note)
