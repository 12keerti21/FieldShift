# FieldShift, explained in plain language

*Rethink What Grows Next.*

No jargon in this document. If you read one page, read the first one.

---

## The problem, in one paragraph

A farmer has to decide what to plant next season. That one decision quietly sets
up the next ten years: how much water the field will need, whether the soil gets
richer or poorer, whether pests build up, whether the ground sits bare and blows
away in the wind.

Farmers already know that changing crops around — **rotation** — matters. What
nobody gives them is a way to compare *their own actual options* on *their own
actual field*, with numbers they can check.

So the decision usually gets made the way it was made last year.

---

## What we built

FieldShift takes a field, pulls **41 years of real weather** for that exact spot
from NASA, looks up what the soil there is like, and then **plays out every
sensible crop rotation** through all 41 years — day by day.

Think of it as asking: *"If you had farmed this field this way since 1984, what
would have actually happened?"* Then asking it again for every other way you
could have farmed it, and laying the answers side by side.

For each option it works out six things:

| What it tracks | In plain terms |
|---|---|
| **Production** | How much food comes off the field |
| **Bad year** | How bad the *worst* years get, not just the average |
| **Water** | How much irrigation you'd have to find |
| **Soil carbon** | Whether your soil is getting richer or poorer |
| **Wind risk** | How exposed your topsoil is to blowing away |
| **Bare ground** | How much of the year the soil sits naked |

Then you tell it what *you* care about — "save water", "most output", "build
soil" — and it re-ranks everything instantly.

---

## What it found

### A field in Kansas, USA

The farmer currently grows wheat one year, then leaves the field empty the next
to save up rainwater. Very common practice there.

Out of **176 possible rotations**, 79 would physically work on that field. And
**four of them beat what he does now on every single measure** — not a trade-off,
nothing given up.

The best one, compared to what he does now:

- **Food off the field more than doubles** (0.90 to 1.99 tonnes per hectare a year)
- Soil carbon **stops falling and starts rising**
- Bare ground drops from **59.8% of the time to 21.5%**
- Wind exposure **cut roughly in half** (774 down to 391 on our index)

### A field in Punjab, India

Here the problem is water. Farmers grow rice in summer and wheat in winter — the
rice is grown in a flooded paddy, which takes enormous amounts of water pumped
from underground.

We went and **measured what's happening to that underground water**. There are
government monitoring wells in the district, some with records going back 27
years. We pulled data from 20 of them.

**Every single one says the water table is dropping — about 1.17 metres a year.**

FieldShift found **seven rotations** that beat rice–wheat on every measure. The
best one for water swaps **one rice year in three** for groundnut, a crop that
grows on rainfall alone.

The result: **24% less irrigation water, while producing slightly more food.**

---

## Why you should believe any of this

This is the part we care most about, and it's where most of the work went.

A computer model that nobody has checked against reality is just a
confident-sounding opinion. So we spent most of our time trying to **prove our own
model wrong.**

### We checked it against things that actually happened

- **Did it get the weather maths right?** We compared our calculations against a
  separate, published scientific dataset. We were off by **3.2%** — which is very
  close.

- **When the model says a crop struggled, did it actually struggle?** We compared
  against 41 years of real government-recorded harvests across four counties in
  three US states. **Every single comparison pointed the right way**, and most
  were strong enough to be statistically meaningful.

- **Did it find the droughts on its own?** We never told it which years were
  droughts. It independently flagged 2012, 2011, 2002, 1988 and 2022 as the worst
  water years. Those are exactly the famous drought years.

### We found seventeen mistakes in our own model

Every one of them was producing confident, believable, **wrong** answers before we
caught it. A few examples in plain terms:

- The model was **watering a bare seedbed from day three** of every season,
  because of how it decided when a crop was thirsty. That alone was inventing
  about a third of the irrigation it reported.

- It was **counting a potato as if it were wheat**. Potatoes are 79% water by
  weight, grain is about 13%. Adding them together made a potato rotation look
  two and a half times more productive than it really was. We only caught this
  when we added India.

- It read a hillslope as **17 times steeper than it is**, because on nearly flat
  ground the wobble in satellite elevation data is bigger than the actual slope.

We list all seventeen in the app. A model whose builders can't tell you its past
mistakes probably hasn't looked for them.

### We went looking for a way to break our own best result — and found one

The India result rests on a model of **flooded rice** we wrote specially. Nothing
had ever checked it, because India doesn't publish the water records we'd need.

So we found somewhere that floods rice the same way and *does* publish them:
**Arkansas**. The US government reports how much water Arkansas rice farmers
actually apply — about **610 to 640 mm** a season.

**Our model asked for 1,135 mm. Nearly twice as much.**

Then we worked out why — or thought we had. Arkansas rice is planted dry in
April and only flooded about five weeks later. Our model flooded the field for
the whole season, paying for 45 days of evaporation and seepage that never
happen. So we rebuilt it: a rice crop can now be dry for part of its life,
flooded for part, and drained before harvest.

That rebuild found **three more mistakes we'd made**, every one of which quietly
created or destroyed water. The books now balance to a trillionth of a
millimetre.

**And it still failed** — 53% too much instead of 77%.

So we kept looking. The answer is somewhere else: the standard crop figures for
rice assume the field is flooded from the day it's sown, so we were charging a
dry seedbed for water evaporating off a pond that isn't there yet. Nobody has
published the right figure for rice that spends its first five weeks dry.

That's where this stops — because the honest answer is "we don't know yet"
rather than a number we made up.

The Punjab rice is *transplanted* into a field that's already flooded, so this
particular mistake doesn't apply there. But the honest summary is: the India
water numbers come from a model we have now tested exactly once, which failed in
a way we understand.

That's more than we knew yesterday. It is not the same as being right.

### And a second test we fail

There's a second one too. There's a well-known benefit to leaving a field empty for a year — it stores up
water for the next crop. Real harvest records say that benefit is about **+10.8%**.
Our model says **+18%**. We over-predict it.

We could have quietly added a fudge factor until the numbers matched. We didn't.

Instead we tested it 18 different ways and found our answer **never** drops to
10.8% no matter what we adjust — which tells us something real is missing from the
science, not that a setting is wrong. We'd rather show you a gap we understand
than hide one we don't.

---

## What it can't do

We put this in the app itself, next to every recommendation — not in fine print.

- **No farmer has used this yet.** Zero field trials. It's validated against
  historical records, not against a season anyone actually farmed with it.

- **The India results are not validated the same way.** We proved the *water
  demand* direction is right against those 20 wells, but we could not get crop
  harvest records for that district. So India is marked **"Tier 2 — transferred,
  not validated"** on every screen, and the app is built so that region
  *literally cannot* claim otherwise.

- **Even in India, the water numbers don't fully add up.** Our model says the
  field should be draining the aquifer 5–13 times faster than the wells show.
  Almost certainly because some irrigation there comes from canals, not pumps —
  and our model doesn't know canals exist. We say so rather than hiding it.
  The *comparison between rotations* still holds; the absolute number doesn't.

- **It doesn't know about money.** No crop prices, no subsidies, no what the
  neighbour is doing, no what the local mill will buy.

- **It doesn't model methane** from flooded rice paddies — which is a big deal for
  climate. So our carbon number flatters rice.

---

## Where the data comes from

| Source | What it gives us |
|---|---|
| **NASA POWER** | 41 years of daily weather — temperature, rain, humidity, wind, sunlight — for any point on Earth |
| **NASA NASADEM** | Ground elevation, so we can work out slope |
| **USDA SSURGO** | Detailed soil survey (United States only) |
| **ISRIC SoilGrids** | Soil estimates for everywhere else in the world |
| **USDA NASS** | 41 years of real recorded harvests, used to check our answers |
| **gridMET** | An independent weather calculation, used to check ours |
| **CGWB / India-WRIS** | Indian government groundwater well readings |

**One honest point about scale:** a single NASA POWER weather square is 52 km
across. For the small Punjab field, that's **108,160 times bigger than the field
itself**. We print that number on screen rather than pretending the data is
sharper than it is.

---

## About the AI

A fair question, so here's the straight answer.

**An AI coding assistant wrote most of the code**, working under human direction.

**But no AI writes the farming advice.** That was deliberate. If an AI generates a
sentence telling a farmer what to plant, an expert can't check *how* it decided —
they can only agree or disagree with it. Every recommendation in FieldShift comes
from physics equations published in scientific literature, and every plain-English
sentence on screen is built directly from those numbers.

You can trace any sentence we show back to the calculation that produced it.

---

## What's actually built, right now

- **6 fields** ready to explore — 5 in the US, 1 in India
- **Any field on Earth** can be analysed on demand. A judge can click a spot on
  the map and get a full answer in **about 30 seconds**
- **A printable one-page field plan** — plain paper, no app needed, goes in the
  tractor cab
- **819 automated tests** that run every time we change anything

---

## The one-sentence version

> FieldShift plays out every way you could farm your field through 41 years of
> real NASA weather, shows you which options beat what you're doing now, and —
> unusually — shows you exactly how much to trust the answer, including the parts
> we got wrong.
