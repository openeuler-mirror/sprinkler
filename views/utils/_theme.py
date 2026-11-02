import streamlit as st


_WARM_CSS = """<style>
    /* ── 温暖极简全局主题 ── */
    .stApp {
        background: #FAFAF8 !important;
    }
    header[data-testid="stHeader"] {
        background: #FAFAF8 !important;
    }

    /* ── 标题：温暖深棕，去掉粗重感 ── */
    .stTitle h1 {
        color: #3D3229 !important;
        font-weight: 600 !important;
        font-size: 1.6rem !important;
        letter-spacing: 0.02em;
    }
    .stSubheader h2, .stSubheader h3 {
        color: #3D3229 !important;
        font-weight: 600 !important;
    }

    /* ── 卡片容器：圆角 + 柔和阴影，无硬边框 ── */
    .stContainer > div[data-testid="stVerticalBlock"] {
        border-radius: 16px !important;
        box-shadow: 0 1px 4px rgba(0,0,0,0.04), 0 4px 16px rgba(0,0,0,0.03) !important;
        border: none !important;
        background: #FFFFFF !important;
        padding: 1.2rem 1.4rem !important;
    }

    /* ── 输入框：暖色调聚焦 ── */
    .stTextInput > div > div > input,
    .stTextArea > div > div > textarea,
    .stNumberInput > div > div > input {
        border-radius: 10px !important;
        border: 1.5px solid #E8E4DF !important;
        background: #FEFEFE !important;
        transition: border-color 0.2s ease, box-shadow 0.2s ease !important;
    }
    .stTextInput > div > div > input:focus,
    .stTextArea > div > div > textarea:focus,
    .stNumberInput > div > div > input:focus {
        border-color: #C9A87C !important;
        box-shadow: 0 0 0 3px rgba(201,168,124,0.12) !important;
    }
    .stTextInput label, .stTextArea label, .stNumberInput label,
    .stSelectbox label, .stMultiselect label, .stRadio label {
        color: #6B6156 !important;
        font-weight: 500 !important;
        font-size: 0.88rem !important;
    }

    /* ── 选择框 ── */
    .stSelectbox > div > div {
        border-radius: 10px !important;
        border: 1.5px solid #E8E4DF !important;
    }
    .stMultiselect > div > div {
        border-radius: 10px !important;
        border: 1.5px solid #E8E4DF !important;
    }

    /* ── Radio 按钮：温暖选中色 ── */
    .stRadio > div[role="radiogroup"] > label[data-baseweb="radio"] > div:first-child {
        border-color: #C9A87C !important;
    }
    .stRadio > div[role="radiogroup"] > label[data-baseweb="radio"] > div:first-child > div {
        background-color: #C9A87C !important;
    }

    /* ── Checkbox ── */
    .stCheckbox > div > div:first-child > label > div:first-child {
        border-color: #C9A87C !important;
        background-color: #C9A87C !important;
    }

    /* ── 主按钮：温暖陶土色 ── */
    .stButton > button[kind="primary"] {
        background: linear-gradient(135deg, #C9A87C, #B8956A) !important;
        border: none !important;
        border-radius: 12px !important;
        color: #FFFFFF !important;
        font-weight: 600 !important;
        font-size: 0.95rem !important;
        letter-spacing: 0.03em;
        box-shadow: 0 2px 8px rgba(184,149,106,0.25) !important;
        transition: all 0.2s ease !important;
    }
    .stButton > button[kind="primary"]:hover {
        box-shadow: 0 4px 16px rgba(184,149,106,0.35) !important;
        transform: translateY(-1px);
    }
    .stButton > button[kind="primary"]:disabled {
        background: #D5CFC6 !important;
        box-shadow: none !important;
        transform: none;
    }
    .stButton > button[kind="secondary"] {
        border-radius: 10px !important;
        border: 1.5px solid #E8E4DF !important;
        color: #6B6156 !important;
        background: transparent !important;
        transition: all 0.2s ease !important;
    }
    .stButton > button[kind="secondary"]:hover {
        background: #F5F3F0 !important;
        color: #3D3229 !important;
        border-color: #D5CFC6 !important;
    }

    /* ── Expander：去掉边框，柔和圆角 ── */
    .streamlit-expanderHeader {
        border-radius: 10px !important;
        background: #F5F3F0 !important;
        color: #6B6156 !important;
        font-weight: 500 !important;
        border: none !important;
    }
    .streamlit-expanderContent {
        border: none !important;
        background: transparent !important;
    }

    /* ── 进度条：温暖渐变 ── */
    .stProgress > div > div > div > div {
        background: linear-gradient(90deg, #C9A87C, #A88B5E) !important;
        border-radius: 6px !important;
    }
    .stProgress > div > div > div {
        background: #EDE9E3 !important;
        border-radius: 6px !important;
    }

    /* ── Status 提示：柔和色块 ── */
    .stAlert[data-baseweb="notification"] {
        border-radius: 10px !important;
        border: none !important;
    }
    [data-testid="stSuccess"] { background: #F0F7F0 !important; color: #3D6B3D !important; }
    [data-testid="stWarning"] { background: #FFF8EC !important; color: #8B6914 !important; }
    [data-testid="stInfo"]    { background: #F5F0E8 !important; color: #6B5D4F !important; }
    [data-testid="stError"]   { background: #FDF2F2 !important; color: #8B3D3D !important; }

    /* ── Code 块：暖灰色背景 ── */
    .stCode {
        border-radius: 10px !important;
    }
    .stCode > div > pre {
        background: #F5F3F0 !important;
        border-radius: 10px !important;
        border: none !important;
    }

    /* ── Spinner ── */
    .stSpinner > div {
        color: #C9A87C !important;
    }

    /* ── 分割线 ── */
    hr {
        border-color: #E8E4DF !important;
    }

    /* ── Dataframe ── */
    .stDataFrame {
        border-radius: 10px !important;
        border: none !important;
    }

    /* ── 微妙的页面呼吸感 ── */
    section[data-testid="stSidebar"] + div {
        padding: 1.5rem 2rem !important;
    }
</style>"""


_TERMINAL_CSS = """<style>
    .cmd-terminal {
        background: #1E1E1E;
        color: #D4D4D4;
        border-radius: 8px;
        padding: 12px 16px;
        font-family: 'Cascadia Code', 'Consolas', 'Courier New', monospace;
        font-size: 0.85rem;
        line-height: 1.5;
        overflow-x: auto;
        white-space: pre-wrap;
        word-break: break-all;
        border: 1px solid #333;
        margin: 0 0 12px 0;
        max-height: 70vh;
        overflow-y: auto;
    }
    .cmd-terminal .cmd-prompt { color: #6A9955; font-weight: 600; }
    .cmd-terminal .cmd-ok { color: #4EC9B0; }
    .cmd-terminal .cmd-fail { color: #F44747; }
    .cmd-terminal .cmd-stdout { color: #D4D4D4; }
    .cmd-terminal .cmd-cmd { color: #DCDCAA; font-weight: 600; background: rgba(220,220,170,0.08); border-radius: 3px; padding: 1px 4px; }
    .cmd-terminal .cmd-stderr { color: #CE9178; }
    .cmd-terminal .cmd-info { color: #569CD6; }
    .cmd-refresh-anim {
        animation: cmd-blink 1s steps(2, start) infinite;
    }
    @keyframes cmd-blink {
        to { visibility: hidden; }
    }
    .stApp > header { height: 0; min-height: 0; }
    .block-container { padding-top: 1rem; }
</style>"""


def inject():
    st.markdown(_WARM_CSS, unsafe_allow_html=True)


def inject_terminal_css():
    st.markdown(_TERMINAL_CSS, unsafe_allow_html=True)
