import io
import re

import streamlit as st

st.set_page_config(
    page_title="洒水车",
    page_icon="🔧",
    layout="wide",
)

HOME_PAGE = "主页"
PAGES = {
    HOME_PAGE: "views.home_page",
    "洒水车一键安装": "views.install_page",
    "命令执行": "views.connect_page",
    "集群设备检查": "views.check_page",
    "打流操作": "views.urma_page",
    "链路查询": "views.link_page",
}

def _sync_superpod_upload():
    uploaded = st.session_state.get("_sidebar_sp_upload")
    if uploaded:
        st.session_state["_shared_sp_bytes"] = uploaded.getvalue()
        st.session_state["_shared_sp_name"] = uploaded.name
    else:
        st.session_state.pop("_shared_sp_bytes", None)
        st.session_state.pop("_shared_sp_name", None)

def main():
    if "page" not in st.session_state:
        st.session_state.page = list(PAGES.keys())[0]

    with st.sidebar:
        st.title("洒水车")
        st.markdown("""<style>
            section[data-testid="stSidebar"] {width: 180px !important; background: #FAFAF8 !important; border-right: 1px solid #E8E4DF !important;}
            section[data-testid="stSidebar"] .stTitle h1 {color: #3D3229 !important; font-weight: 600 !important; font-size: 1.15rem !important; letter-spacing: 0.02em;}
            section[data-testid="stSidebar"] .stButton > button {border-radius: 10px !important; transition: all 0.2s ease !important; font-weight: 500 !important; border: none !important; color: #6B6156 !important; background: transparent !important;}
            section[data-testid="stSidebar"] .stButton > button:hover {background: #F5F3F0 !important; color: #3D3229 !important; transform: none !important;}
            section[data-testid="stSidebar"] .stButton > button[kind="primary"] {background: linear-gradient(135deg, #C9A87C, #B8956A) !important; color: #FFFFFF !important; box-shadow: 0 2px 8px rgba(184,149,106,0.2) !important;}
            section[data-testid="stSidebar"] .stButton > button[kind="primary"]:hover {box-shadow: 0 4px 12px rgba(184,149,106,0.3) !important; transform: translateY(-1px) !important;}
        </style>""", unsafe_allow_html=True)
        for name in PAGES:
            if st.button(name, use_container_width=True, type="primary" if name == st.session_state.page else "secondary"):
                st.session_state.page = name
                st.rerun()
        st.divider()
        st.file_uploader(
            "超节点文件",
            type=["xlsx"],
            key="_sidebar_sp_upload",
            on_change=_sync_superpod_upload,
        )

    main_placeholder = st.empty()
    module_name = PAGES[st.session_state.page]
    import importlib
    module = importlib.import_module(module_name)
    with main_placeholder.container():
        module.render()

if __name__ == "__main__":
    main()
