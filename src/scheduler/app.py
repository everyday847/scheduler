import streamlit as st
from streamlit_tags import st_tags

try:
    from .date_to_week_index import vacation_date_to_week_index
    from .service import DEFAULT_SHIFTS
    from .main import optimize_schedule
except ImportError:  # pragma: no cover - supports running from src/scheduler
    from date_to_week_index import vacation_date_to_week_index
    from service import DEFAULT_SHIFTS
    from main import optimize_schedule

st.markdown("# People")

fellow_groups = {
    "NCC_JR": st_tags(label="NCC_JR fellows:", text="Press enter to add more", value=[]),
    "NCC_SR": st_tags(label="NCC_SR fellows:", text="Press enter to add more", value=[]),
    "STROKE": st_tags(label="STROKE fellows:", text="Press enter to add more", value=[]),
    "CCM": st_tags(label="CCM fellows:", text="Press enter to add more", value=[]),
}
request_fellows = [
    fellow
    for group_name in ("NCC_JR", "NCC_SR", "STROKE")
    for fellow in fellow_groups[group_name]
]

W = 52

# st.write(keywords)

with st.expander("# Rules"):

    ncc_fellows_assigned_fully = st.checkbox("Assign entire 52-week schedule for jr and sr NCC fellows")
    # st.checkbox("Fellows can have only one rotation per week")
    col1, col2 = st.columns(2)
    with col1:
        ncc_shifts_covered = st.checkbox("All NCC shifts must be covered")
    with col2:
        if ncc_shifts_covered:
            swing_deficit = st.number_input("Except for this many swing shifts covered by ad hoc rotation", value=0, step=1)

    max_consecutive_icu = st.number_input("Fellows may have this many core ICU shifts in a row", min_value=2, max_value=20, value=8, step=1)
    jr_start_micu = st.checkbox("Junior fellows start with MICU their first month")
    jr_ncc_before_week = st.number_input("Junior fellows should hit NCC before this week", min_value=0, max_value=52, value=19, step=1)
    jr_ncc_before_swing = st.number_input("Junior fellows should have this many NCC shifts before swing", min_value=0, max_value=12, value=4, step=1)

    # custom requests like "the fourth block needs two micu fellows
    blocked_up = st.checkbox("Shifts are 'blocked': into four or two week chunks.")

    ncc_oversight = st.checkbox("At least one NCC team should have an NCC or Stroke fellow at all times.")

with st.expander("## Vacation"):

    vacation_requests = {}
    for f in request_fellows:
        vacation_requests[f] = []
        for it in range(4):
            vacation_requests[f].append(
                vacation_date_to_week_index(st.date_input(f"Vacation for {f} week {it+1}:").timetuple()))

vacation_requests = {
    k: [v_ for v_ in v if v_ > 0]
    for k,v in vacation_requests.items()
}

R = DEFAULT_SHIFTS

def bg_color(x):
    color = '#FFFFFF'
    if x == 'Swing': color = '#A9D08E'
    if x == 'Stroke': color = "#F8CBAD"
    if x == 'Vasc/Clin': color = "#FCE4D6"
    if x == 'MICU': color = "#B4C6E7"
    if x in {'NCC1', 'NCC2'}: color = "#C6E0B4"
    if x == 'SICU': color = "#FFE699"
    if x == 'NS': color = "#FFF3CC"
    if x == 'Anaesthesia': color = "#F8CBAD"

    if x in fellow_groups["NCC_JR"] or x in fellow_groups["NCC_SR"]: color = '#C6E0B4'
    if x in fellow_groups["STROKE"]: color = '#F8CBAD'
    if x in fellow_groups["CCM"]: color = '#B4C6E7'

    return f'background-color: {color}'

# import pandas
# df1 = pandas.DataFrame.from_dict([
#     {
#         'Week': ii,
#         'Joe': R[ii],
#     }
#     for ii in range(len(R))])
#
# st.dataframe(df1.style.applymap(lambda x: bg_color(x)), #[{'selector': 'MICU', 'props': 'background-color: #e6ffe6;'}]),
#              hide_index=True)

# don't bother using options yet.
if st.button("optimize"):
    with st.spinner():
        shifts_for_fellows, fellows_for_shifts = optimize_schedule(
            fellow_groups=fellow_groups,
            shifts=R,
            fellow_week_pairs=vacation_requests
        )

        import pandas
        df1 = pandas.DataFrame.from_dict([
            {
                'Week': ii,
                **{fellow: shifts_for_fellows[fellow][ii] for fellow in request_fellows}

            }
        for ii in range(W)])

        st.dataframe(df1.style.applymap(lambda x: bg_color(x)), #[{'selector': 'MICU', 'props': 'background-color: #e6ffe6;'}]),
             hide_index=True)

        df2 = pandas.DataFrame.from_dict([
            {
                'Week': ii,
                **{s: fellows_for_shifts[s][ii] for s in ['NCC1', 'NCC2', 'Extra', 'Swing']},
            }
        for ii in range(W)])

        st.dataframe(df2.style.applymap(lambda x: bg_color(x)), #[{'selector': 'MICU', 'props': 'background-color: #e6ffe6;'}]),
             hide_index=True)
