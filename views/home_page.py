import streamlit as st

from views.utils._theme import inject as _inject_warm_theme

_PAGE_CARDS = [
    {
        "icon": "📦",
        "title": "洒水车一键安装",
        "page": "洒水车一键安装",
        "desc": "在目标节点上批量安装 Sprinkler 工具。支持通过超节点文件或手动输入 IP 选择目标，配置代理和 CodeHub 凭据后一键远程安装，自动检测是否已安装并跳过。",
    },
    {
        "icon": "💻",
        "title": "命令执行",
        "page": "命令执行",
        "desc": "在远程节点上批量执行自定义命令。支持多种连接模式（CPU OS、CPU BMC、NPU BMC 等），可配置连接超时、并行度等参数，实时查看执行过程和结果。",
    },
    {
        "icon": "🔍",
        "title": "集群设备检查",
        "page": "集群设备检查",
        "desc": "对集群设备进行健康检查，包括子卡注册、配置信息、端口状态、建链状态、信用证、磁盘空间、连接数等检查项。支持 CPU / NPU / SFU 多种环境类型，按节点分组展示结果。",
    },
    {
        "icon": "📡",
        "title": "打流操作",
        "page": "打流操作",
        "desc": "执行 URMA 带宽打流测试，支持板内打流和跨板打流。可配置模式（h2d/d2h/d2d/all/xd2d）、测量时长、EID 模式、链路选择等参数。还支持免密配置和 SSH 破解操作。",
    },
    {
        "icon": "🔗",
        "title": "链路查询",
        "page": "链路查询",
        "desc": "查看 URMA EID 链路对信息，支持多种模式（h2d/d2h/d2d）",
    },
]


def render():
    _inject_warm_theme()

    st.markdown(
        "<h1 style='color: #3D3229; font-weight: 600; font-size: 1.8rem; margin-bottom: 0.3rem;'>洒水车</h1>",
        unsafe_allow_html=True,
    )
    st.markdown(
        "<p style='color: #6B6156; font-size: 1rem; margin-bottom: 1.5rem;'>集群运维工具集 — 安装、检查、执行、打流一站式操作</p>",
        unsafe_allow_html=True,
    )

    cols = st.columns(2, gap="medium")
    for i, card in enumerate(_PAGE_CARDS):
        with cols[i % 2]:
            with st.container(border=True):
                col_icon, col_text = st.columns([0.08, 0.92], gap="small", vertical_alignment="top")
                with col_icon:
                    st.markdown(
                        f"<div style='font-size: 1.6rem; line-height: 1.8rem;'>{card['icon']}</div>",
                        unsafe_allow_html=True,
                    )
                with col_text:
                    st.markdown(
                        f"<div style='color: #3D3229; font-weight: 600; font-size: 1.05rem; margin-bottom: 0.2rem;'>{card['title']}</div>",
                        unsafe_allow_html=True,
                    )
                    st.markdown(
                        f"<div style='color: #6B6156; font-size: 0.88rem; line-height: 1.5;'>{card['desc']}</div>",
                        unsafe_allow_html=True,
                    )
                if st.button(f"进入 →", key=f"home_go_{i}", use_container_width=True):
                    st.session_state.page = card["page"]
                    st.rerun()
