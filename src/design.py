"""Local visual system; CSS targets owned classes and explicit container keys."""

from pathlib import Path
import streamlit as st

MARK=Path(__file__).resolve().parents[1]/'assets/brand-mark.svg'


def expert():
    return st.session_state.get('presentation_mode','Guided')=='Expert'


def apply_design():
    st.html('''<style>
    :root { --aei-navy:#142C48; --aei-teal:#177E91; --aei-line:#D7E1EB; }
    h1,h2,h3,h4 { color:var(--aei-navy); letter-spacing:-.025em; }
    h1 { font-size:2rem; } h2 { font-size:1.5rem; } h3 { font-size:1.2rem; }
    .aei-brand { display:flex; align-items:center; gap:14px; margin-bottom:0; }
    .aei-brand svg { width:48px; height:48px; flex-shrink:0; }
    .aei-name { color:var(--aei-navy); font-size:28px; font-weight:750; letter-spacing:-1px; line-height:1.1; }
    .aei-subtitle { color:#365169; font-size:14px; margin-top:5px; }
    .aei-badge { display:inline-block; color:#795014; background:#FFF4DF; border:1px solid #D8A04C;
      border-radius:6px; padding:3px 9px; font-size:13px; font-weight:650; }
    .aei-source { color:#142C48; font-size:14px; margin:6px 0 0; }
    .aei-kicker { color:#177E91; font-size:13px; font-weight:700; letter-spacing:.08em; text-transform:uppercase; }
    .aei-unit-name { color:#142C48; font-weight:650; font-size:16px; margin:0 0 4px; }
    .aei-unit-detail { color:#365169; font-size:14px; line-height:1.5; margin:3px 0; }
    .aei-culture-count { font-size:28px; font-weight:700; color:#142C48; line-height:1.2; margin-top:10px; }
    .aei-unit-caption { font-size:13px; color:#365169; line-height:1.5; }
    .aei-unit-selection { height:20px; font-size:13px; font-weight:650; color:#136575; }
    .aei-finding { color:#795014; font-size:13px; font-weight:650; margin-bottom:6px; }
    .aei-supported { color:#136575; }
    .aei-overview-title { margin:8px 0; font-size:24px; font-weight:700; }
    .aei-intro { color:#142C48; font-size:16px; margin:0; }
    .aei-finding-title { color:#142C48; font-size:15px; font-weight:650; margin:8px 0; }
    .aei-finding-values { color:#365169; font-size:14px; line-height:1.5; }
    .st-key-app_header { background:#FFFFFF; border:1px solid var(--aei-line); border-radius:12px;
      padding:10px 14px; margin-top:-2rem; margin-bottom:6px; }
    .st-key-overview_metrics { background:#FFFFFF; border-radius:10px; }
    .st-key-overview_units [class*="st-key-unit_"] { background:#FFFFFF; }
    .st-key-overview_units [class*="st-key-unit_"]:has(.aei-unit-selected) { background:#EAF5F6; border-color:#177E91; }
    .st-key-overview_units [class*="st-key-unit_"] button { min-height:54px; font-size:16px; }
    .st-key-overview_units [class*="st-key-unit_"] button:focus-visible { outline:3px solid #142C48; outline-offset:3px; }
    .st-key-overview_side [class*="st-key-overview_finding_"] { background:#FFFFFF; }
    .st-key-data_status,.st-key-selected_department { background:#FFFFFF; }
    .st-key-overview_units,.st-key-overview_side { min-width:0; }
    #aei-top { scroll-margin-top:100px; }
    .st-key-overview_metrics .stColumn { min-width:200px; }
    @media (max-width:1100px) {
      .stColumn:has(.st-key-overview_units),.stColumn:has(.st-key-overview_side) {
        flex-basis:100%; min-width:100%; width:100%;
      }
    }
    @media (max-width:700px) { .aei-name {font-size:24px;} .aei-brand svg {width:40px;height:40px;} }
    </style>''')


def brand():
    st.html('<h1 id="aei-top" class="aei-name" style="margin:0">AEI Sentinel</h1>')
