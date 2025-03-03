import json
from z3 import EnumSort #String, EnumSort
import math

# Pseudocode description of rules:
#  NCC


# o There is also a swing fellow on NCC, making a total of three fellows per day
# assigned at a time. Night calls will be arranged separately by me
#
#  Overall rules
# o First-year fellows should have their first block of NCC no later than November

from z3 import *

jr_fellows = ["NCC Raya", "NCC Joseph"]
sr_fellows = ["NCC David", "NCC Prash"]
stroke_fellows = ["Stroke Arthur", "Stroke Betty", "Stroke Charles","Stroke Deirdre"]
CCM_fellows = ["CCM Ariana", "CCM Bert", "CCM Chloe","CCM Dennis","CCM Edwina", "CCM Frank","CCM George","CCM Helen","CCM Iago","CCM Jake","CCM Kyle","CCM Liana","CCM Mary","CCM Ning","CCM Oyo"]

num_NCC_jr_fellows = len(jr_fellows)
num_NCC_sr_fellows = len(sr_fellows)
num_stroke_fellows = len(stroke_fellows)
num_CCM_fellows = len(CCM_fellows)
N = num_NCC_jr_fellows + num_NCC_sr_fellows + num_stroke_fellows + num_CCM_fellows  # Number of fellows (example)


W = 52  # Number of weeks
R = ["NCC1", "NCC2", "Swing", "SICU", "MICU", "Elec", "Vac", "NS", "Vasc/Clin", "Anaesthesia"]  # Example rotations

# A 3D boolean variable: x[f, w, r] is True if fellow f is assigned to rotation r in week w
x = {
    (f, w, r): Bool(f"x_{f}_{w}_{r}") for f in range(N) for w in range(W) for r in R
}

# s = Solver()
o = Optimize()

def ncc_fellows_assigned_fully(o):
    # Each NCC fellow has exactly one rotation per week, because we are responsible for their scheduleo.
    for f in range(num_NCC_jr_fellows + num_NCC_sr_fellows):
        for w in range(W):
            o.add(AtLeast(*[x[f, w, r] for r in R], 1))


def everyone_one_rotation_per_week(o):
    # Each other fellow has at most one rotation per week, since we are only assigning their NCC time.
    for f in range(N):
        for w in range(W):
            o.add(AtMost(*[x[f, w, r] for r in R], 1))


def ncc_shifts_covered_swing_deficit(o, deficit):
    # There is one fellow on Swing and at least one fellow on NCC1, NCC2 per week.
    # We can be more specific if this gets nuts with overassignment.
    for w in range(W):
        o.add(Sum([If(x[f, w, "NCC1"], 1, 0) for f in range(N)]) >= 1)
        o.add(Sum([If(x[f, w, "NCC2"], 1, 0) for f in range(N)]) >= 1)
        o.add(Sum([If(x[f, w, "NCC1"], 1, 0) for f in range(N)]) <= 2)
        o.add(Sum([If(x[f, w, "NCC2"], 1, 0) for f in range(N)]) <= 2)
        # At most one extra fellow on at once.
        o.add(Sum([If(Or(x[f, w, "NCC1"], x[f, w, "NCC2"]), 1, 0) for f in range(N)]) <= 3)

        # LE because inadequacy
        o.add(Sum([If(x[f, w, "Swing"], 1, 0) for f in range(N)]) <= 1)

    # Ah, we might not actually have enough swing. Let's say at most 8 weeks are
    # unassigned.
    o.add(Sum([
        Sum([If(x[f, w, "Swing"], 1, 0) for f in range(N)])
        for w in range(52)]) >= 52-deficit)

def ncc_stroke_oversight(o):
    # IDEALLY every week either NCC1 or NCC2 is neurocrit or stroke.
    for w in range(W):
        # o.add(
        #     Sum([If(x[f, w, "NCC1"], 1, 0) for f in
        #          range(num_NCC_jr_fellows + num_NCC_sr_fellows + num_stroke_fellows)]) +
        #     Sum([If(x[f, w, "NCC2"], 1, 0) for f in
        #          range(num_NCC_jr_fellows + num_NCC_sr_fellows + num_stroke_fellows)]) >= 1)

        # TODO: this constraint seems to be enforced inconsistently, but maybe that's because it's hard.
        # o.add(
        #     Or(
        #         Sum([
        #             If(x[f, w, "NCC1"], 1, 0) for f in
        #             range(num_NCC_jr_fellows + num_NCC_sr_fellows + num_stroke_fellows)
        #         ]) >= 1,
        #         Sum([If(x[f, w, "NCC2"], 1, 0) for f in
        #              range(num_NCC_jr_fellows + num_NCC_sr_fellows + num_stroke_fellows)]) >= 1))
        o.add(Sum([
                    If(Or(x[f, w, "NCC1"], x[f, w, "NCC2"]), 1, 0) for f in
                    range(num_NCC_jr_fellows + num_NCC_sr_fellows + num_stroke_fellows)
                ]) >= 1)

def maximum_consecutive_icu_shifts(o, MAX_CONSEC):

    for f in range(N):
        for w in range(W - MAX_CONSEC):
            # for r in ["NCC1", "NCC2", "Swing", "SICU", "MICU"]:
            #     o.add(Sum([If(x[f, w + i, r], 1, 0) for i in range(MAX_CONSEC + 1)]) <= MAX_CONSEC)
            o.add(Sum([If(
                Or(*[x[f, w + i, r] for r in ["NCC1", "NCC2", "Swing", "SICU", "MICU"]]),
                1,
                0) for i in range(MAX_CONSEC + 1)]) <= MAX_CONSEC)


def min_consecutive_icu_shifts(o, n):
    # We don't want to put anyone on for 'singlet' weeks of NCC service.
    # MIN_CONSEC = 2
    for f in range(N):
        for w in range(1, W - 1):
            # What we need to express is 'no singlets'. What does that look like
            # with the operations I have access to? I think it's: for any window
            # of 3 *centered on* an r, the sum is greater than 1.
            nccs = ["NCC1", "NCC2", "Swing"]
            o.add(
                Sum([
                    If(
                        x[f,w,r],
                        Sum([
                            *[If(x[f, w-1, r], 1, 0) for r in nccs],
                            *[If(x[f, w+1, r], 1, 0) for r in nccs],
                            *[If(x[f, w, r], 1, 0) for r in nccs],
                        ]),
                        1000 # always bigger than 2
                    ) # we can sum over ifs because we know we are mutually exclusive: fwr is true for only one r
                for r in nccs]) >= n)
            # for r in :
            #     o.add(Sum([If(x[f, w + i, r], 1, 0) for i in range(MIN_CONSEC + 1)]) >= MIN_CONSEC)

def jr_first_month_micu(o):
    # jr fellows first month is MICU
    for f in range(num_NCC_jr_fellows):
        for w in range(4):
            o.add(x[f, w, "MICU"])

def jr_ncc_before_19(o):
    # jr fellows have a block of NCC before week 19
    for f in range(num_NCC_jr_fellows):
        o.add(
            Sum([If(Or(x[f, w, "NCC1"], x[f, w, "NCC2"]), 1, 0) for w in range(4, 19)]) >= 1
        )

def ccm_total_service(o):
    # ccm fellows have precisely one month of NCC, of which one week is swing
    # TODO: follow 'block' boundaries
    for f in range(num_NCC_jr_fellows + num_NCC_sr_fellows + num_stroke_fellows, N):
        # oh, and it's consecutive.
        # actually to do this, it's just as easy to do blocks

        # Total for the year
        o.add(Sum([If(Or(x[f, w, "NCC1"], x[f, w, "NCC2"]), 1, 0) for w in range(52)]) == 3)
        o.add(Sum([If(x[f, w, "Swing"], 1, 0) for w in range(52)]) == 1)

        # Consecutivity
        if True:
            for w in range(0, W, 4):
                # If the first week of the block is NCC-ish, the rest must be.
                # Oh no that is insane logic. Either all or none of the block is NCC-ish.
                o.add(
                    If(
                        Sum([If(Or(x[f, w_, "NCC1"], x[f, w_, "NCC2"], x[f, w_, "Swing"]), 1, 0) for w_ in range(w, w + 4)]) > 0,
                        Sum([If(Or(x[f, w_, "NCC1"], x[f, w_, "NCC2"], x[f, w_, "Swing"]), 1, 0) for w_ in
                             range(w, w + 4)]),
                        4) == 4 # if the whole block isn't NCCish, this doesn't fail condition
                )
                o.add(
                    If(
                        Sum([If(Or(x[f, w_, "NCC1"], x[f, w_, "NCC2"], x[f, w_, "Swing"]), 1, 0) for w_ in
                             range(w, w + 4)]) > 0,
                        Sum([If(x[f, w_, "Swing"], 1, 0) for w_ in
                             range(w, w + 4)]),
                        1) == 1  # if the whole block isn't NCCish, this doesn't fail condition
                )

def total_shift_service(o, f, shift, n):
    o.add(Sum([If(x[f, w, shift], 1, 0) for w in range(52)]) >= n)

def total_nicu_service(o, f, shift, n):
    o.add(Sum([If(Or(x[f, w, "NCC1"], x[f, w, "NCC2"]), 1, 0) for w in range(52)]) >= n)

def stroke_total_service(o):
    for f in range(num_NCC_jr_fellows+num_NCC_sr_fellows, num_NCC_jr_fellows+num_NCC_sr_fellows+num_stroke_fellows):
        total_shift_service(o, f, "Swing", 2)
        total_nicu_service(o, f, "Swing", 6)

ncc_fellows_assigned_fully(o)
everyone_one_rotation_per_week(o)
ncc_shifts_covered_swing_deficit(o, 8)
maximum_consecutive_icu_shifts(o, 8)
min_consecutive_icu_shifts(o, 2)
jr_first_month_micu(o)
jr_ncc_before_19(o)

"""
sum over each fellow.
"""
ccm_total_service(o)
stroke_total_service(o)




if False:

    # jr fellows have 4x NCC before their first swing
    for f in range(num_NCC_jr_fellows):
        o.add(Min([If(x[f, w, "NCC1"], 1, 0) for w in range(4, 19)]) + Sum(
            [If(x[f, w, "NCC2"], 1, 0) for w in range(4, 19)]) >= 1)



o.check()
    # print(type(o.check()))
    # if not o.check: quit()
    # quit()
    # if o.check() != Z3_L_TRUE: quit()


m = o.model()

fellows = jr_fellows+sr_fellows+stroke_fellows+CCM_fellows
shifts = {
    fellow: ["" for w in range(W)] for fellow in fellows
}

fellows_on_ncc = {
    'NCC1': [[] for w in range(W)],
    'NCC2': [[] for w in range(W)],
    'Swing': [[] for w in range(W)],
}

for d in m.decls():
    if m[d]:
        _, f, w, s = d.name().split('_')
        shifts[fellows[int(f)]][int(w)] = s
        if s in fellows_on_ncc:
            fellows_on_ncc[s][int(w)].append(fellows[int(f)])
        # print ("%s = %s" % (d.name(), m[d]))

# print out total schedules for the NCC fellows
print('    ,' + ','.join(fellows[:num_NCC_jr_fellows + num_NCC_sr_fellows]))
for w in range(W):
    print(f'{w: 4d},', end='')
    for i in range(num_NCC_jr_fellows + num_NCC_sr_fellows):
        print(shifts[fellows[i]][w], end=",")
    print()

# print out total schedules for the NCC fellowship shifts
print('    ,' + ','.join(['NCC1', 'NCC2', 'Swing']))
for w in range(W):
    print(f'{w: 4d} ', end='')
    for s in ['NCC1', 'NCC2', 'Swing']:
        print('+'.join(fellows_on_ncc[s][w]), end=",")
    print('\n')

