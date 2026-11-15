# FieldShift — demo script

*Written to be spoken, not read aloud verbatim. Timings assume a live tool on
screen. If anything breaks, the printable field plan is the fallback — it is a sheet
of paper and it cannot fail.*

---

## Before you start

```bash
cd ~/rotation-dss && ./scripts/serve.sh
```

Open <http://127.0.0.1:8000>. **Click Finney County and let it load.** Have a second
tab already showing a printed field plan, so you never have to wait for it live.

Have ready, in case someone asks: `docs/DECISIONS.md` and `python -m pytest`.

---

## 30 seconds — the version that fits in a corridor

> "Most crop rotation tools tell you what to plant where. The problem is that a
> rotation benefit doesn't exist crop by crop — it only shows up across a sequence,
> over years.
>
> So we simulate the soil forward through forty-one years of weather that actually
> happened. Water, nitrogen, carbon, all carried from one crop to the next.
>
> And then we checked whether that's real. USDA publishes wheat yields separately
> for fields that followed a fallow versus fields cropped continuously — that's an
> *observed* rotation effect, about eleven percent. Our model, with nothing fitted
> to that number, gives eighteen. Not perfect. But a tool that scored crops
> independently would show no difference at all."

---

## 90 seconds — the demo

**[map on screen]**

> "Four fields across Kansas, Nebraska and Texas. Let's take this one — Finney
> County, western Kansas, over the Ogallala aquifer."

**[click Finney, panel loads]**

> "Everything here is real. Forty-one years of NASA POWER weather. The soil comes
> from the USDA survey — this is a Richfield silt loam holding 212 millimetres of
> plant-available water per metre."

**[point at the blue callout]**

> "This farmer runs winter wheat and summer fallow. We examined 176 possible
> rotations, 79 were physically possible here, and **four of them beat his current
> rotation on every single measure.** Not a trade-off — nothing given up."

**[click the top rotation]**

> "Against continuous wheat: production roughly doubles, from 0.90 to 1.99 tonnes of
> dry matter a hectare a year. And look at carbon — his field is currently
> **losing** it, minus 0.05 a year. This builds it, plus 0.27. Bare ground drops
> from sixty percent of the time to twenty-one."

**[drag the irrigation slider]**

> "Now — say water is what he cares about most."

**[list reorders instantly]**

> "That's re-ranking against his priorities in about ten milliseconds, because the
> physics is already simulated. The expensive half doesn't depend on what he wants;
> the half that does is instant."

**[point at the yellow box]**

> "And it tells him when the answer is fragile. Right now it's saying the top choice
> would change if he weighted carbon differently — so the model is being honest that
> it isn't a clear winner."

**[click Printable field plan]**

> "And this is what he actually takes away. A calendar showing exactly when his
> field is bare, the numbers that change, the risks counted from forty-one years,
> and the questions only his agronomist can answer. No JavaScript — it prints the
> same anywhere, and a sheet of paper works in a tractor cab."

---

## The question you should invite: "what doesn't work?"

**Lead with this.** Most teams won't, and it's what makes everything else
believable.

> "A few things, and we put them in the output rather than hiding them.
>
> The big one is wind. Our erosion model only does water erosion, and this field is
> almost dead flat, so it reports basically no soil loss — which sounds great and is
> misleading. This is the Dust Bowl region; the real mechanism is wind. So the tool
> computes a wind index from the same weather and says plainly that the erosion
> number is for the wrong mechanism.
>
> We also found eight genuine bugs in our own model *by* validating it. One was a
> slope calculation wrong by seventeen times, because on flat ground the noise in
> the elevation data is bigger than the actual slope.
>
> And one embarrassing one. Early on I claimed our evapotranspiration was eighteen
> percent too high. That came from a number I half-remembered, not measured. When we
> checked against a real published dataset, there was no bias at all. So we withdrew
> it, and made a rule: a validation target has to be data we fetched or a citation
> we can point at. Never a remembered number."

---

## If they ask: "did it work anywhere else?"

> "Four counties, three states, no adjustment between them. Same code, same crop
> table. The Nebraska site has a sandy soil holding a quarter of the water the
> Kansas one does — that's the real test. Skill against observed yields stayed
> positive at eleven of eleven dryland crop–site combinations, eight of them
> statistically significant.
>
> And we perturbed twenty parameters to the edges of their plausible ranges to check
> the conclusion wasn't an artefact. It held every time."

## If they ask: "where's the AI?"

> "It wrote most of the code. But there is deliberately **no AI in the decision
> path** — no language model touches a recommendation. Every number comes from a
> published equation applied to an observed dataset, and the evidence card names
> which one.
>
> We did that because an agronomist can't audit a language model, and an
> unauditable agronomic recommendation isn't decision support."

## If they ask: "is this usable today?"

Be straight.

> "For these six fields, yes — you can print a plan right now. For a new field it's
> a minute of computation, which we'd precompute rather than make someone wait.
>
> What it still needs before a farmer should act on it: an agronomist to check our
> pathogen intervals, local prices, and a soil test. The tool says all three on
> every output."

---

## One line to end on

> "Everything I've told you, you can re-run yourself. There's a command for each
> claim."

---

## If the demo breaks

1. Open the already-printed field plan tab. Talk through that instead — it carries
   the whole story.
2. Failing that: `PYTHONPATH=src python scripts/search_rotations.py` in a terminal
   prints the search and the trade-offs in about a minute.
3. Failing that, the numbers in `docs/SUBMISSION.md` are all you need.
