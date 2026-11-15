# FieldShift — the full spoken script

*Read this. Plain words, no jargon. Lines in quotes are what you say.
App running at **http://127.0.0.1:8000**.*

---

## 0 · THE HOOK (20 seconds, before you touch anything)

> "A farmer has to decide what to plant next season.
>
> That one decision quietly sets up the next ten years — how much water the
> field will need, whether the soil gets richer or poorer, whether the ground
> sits bare and blows away in the wind.
>
> Farmers already know that changing crops around matters. What nobody gives
> them is a way to compare *their* options on *their* field, with numbers they
> can actually check.
>
> So the decision usually gets made the way it was made last year.
>
> That's the problem we picked. Here's what we built."

---

## 1 · WHAT IT IS (30 seconds)

> "FieldShift takes a field, pulls **41 years of real NASA weather** for that
> exact spot, looks up what the soil is like, and then **plays out every
> sensible way of farming it** — day by day, through all 41 years.
>
> Think of it as asking: *if you had farmed this field this way since 1984,
> what would actually have happened?* Then asking it again for every other way
> you could have farmed it, and laying the answers side by side.
>
> That's about **a million days of farming simulated per field.**"

**For each option it tracks six things — say them plainly:**

> "How much food comes off. How bad the worst years get. How much water you'd
> have to find. Whether your soil is getting richer or poorer. How exposed your
> topsoil is to blowing away. And how much of the year the soil sits naked."

---

## 2 · THE WALKTHROUGH

### Screen: **Field** (opens on Finney County, Kansas)

**Point at the big green box.**

> "This is the whole product in one box.
>
> We tried **176 different ways** to farm this field. **79** of them would
> really work here. And **four of them beat what this farmer does now in every
> single way we measure.**
>
> Not a trade-off. Nothing given up."

**Point at the three tiles.**

> "The sky gives this field 541 millimetres of rain a year. The air wants to
> take three times that. So here, every farming decision is really a water
> decision — and the tool says so in plain English."

---

### Screen: **Rotations**

**Click "Save water". Then click "Most output". The list re-orders instantly.**

> "You tell it what *you* care about, and the answer moves immediately.
>
> That's deliberate engineering. All the heavy science is done in advance, so
> what's left is just applying your priorities. **The slow part is physics. The
> fast part is preference.** A farmer exploring trade-offs shouldn't wait."

**Point at the coloured calendar bars.**

> "These show what's growing when, across the whole cycle. The gaps are months
> where the soil is bare and blowing away.
>
> A list of crop names hides that completely. This doesn't."

**Point at the coloured dots next to each number.**

> "Every measure has its own colour — blue is water, brown is soil, green is
> the crop. So you can scan for the one you care about before reading a single
> label."

---

### Screen: **Compare**

> "What he does now, side by side with what we suggest. Same field, same
> weather, same soil — the only difference is what gets planted and when.
>
> Green marks the better number in each row. We show **all six**, because one
> overall score would hide the thing that actually matters."

**The headline numbers to say out loud:**

> "Food off the field **more than doubles.** The soil **stops getting poorer
> and starts getting richer.** Bare ground drops from **60% of the year to
> 21%.** Wind exposure **halves.**"

---

### Screen: **Evidence** — *click a card, it flips over*

> "Every single suggestion comes with this.
>
> Where each number came from. How rough that data is. What we **can't** see.
> And who needs to check it before anyone acts on it.
>
> And here's the bit I like: our confidence score is set by our **worst** piece
> of information, never the average. **One good source can't cover for a bad
> one.**"

---

### Sidebar: switch to **Sangrur District, Punjab**

> "Now the same tool, on the other side of the world.
>
> Here farmers grow rice in summer and wheat in winter. The rice is grown in a
> flooded field, and the water is pumped from underground.
>
> We went and checked what that's doing. **Twenty government monitoring wells**,
> some with 27 years of records. **Every single one** says the water underneath
> is dropping about **1.17 metres every year.**
>
> We found **seven** better options. The best one swaps one rice year in three
> for a crop that grows on rainfall alone —
>
> **a quarter less water, and slightly more food.**"

**Point at the orange banner.**

> "And it says right here that this one **isn't proven yet**. We built the
> program so that this field *cannot* claim otherwise — if you tried to make it,
> the software wouldn't even start."

---

### Screen: **Climate stress** — *click "3 °C warmer, 20% less rain", wait ~25 s*

> "Would our advice still hold if the weather got worse?
>
> This replays the same 41 years, three degrees hotter and a fifth drier, and
> re-runs everything from scratch.
>
> It's a **what-if, not a prediction.** We don't have a climate model and we're
> not pretending to."

**When it lands:**

> "His current farming loses nearly a third of its output, and the soil degrades
> twice as fast.
>
> But the number of better options goes **up** — from four to five.
>
> **The reason to change gets stronger, not weaker.**"

---

### Screen: **Analyse a field** — *the showstopper, if there's time*

> "And it isn't limited to the fields we prepared. **Pick a field. Anywhere on
> Earth.**"

**Let them click the map.** *(Choose somewhere in the USA — the worldwide soil
service rate-limits.)*

> "It's downloading 41 years of NASA weather for that exact spot right now.
> Then the soil survey. Then it simulates every workable rotation through every
> one of those years.
>
> About half a minute."

**When it finishes:**

> "A field nobody has ever looked at, fully analysed, with a printable plan at
> the end. **Thirty-nine seconds.**"

---

## 3 · THE CATCHY POINTS — drop these anywhere

**💧 The honesty number**
> "One NASA weather pixel covers 52 kilometres. That's **108,000 times** the
> size of that little Punjab field. We print that on the screen. Most tools
> would hide it."

**🛰️ The satellite caught *our* mistake**
> "We checked our ground-cover numbers against NASA satellite images. Four of
> our five American fields failed — because the satellite showed those spots
> aren't growing what we assumed. One of them is **cattle pasture, not
> farmland.** The satellite caught our mistake, not the model's."

**🥔 We were counting a potato as if it were wheat**
> "A potato is 79% water. Grain is 13%. We were adding them together — which
> made one plan look **two and a half times more productive** than it really
> was. We only caught it when we added India."

**🤖 An AI built it, but no AI gives the advice**
> "An AI wrote most of our code. **No AI writes the farming advice** —
> deliberately. If a chatbot tells a farmer what to plant, an expert can't check
> *how* it decided. Every sentence we show traces back to physics you can look
> up."

**📄 The farmer walks away with paper**
> "The final output is a **one-page printed plan** — a month-by-month calendar.
> No app, no login, no signal. A dashboard needs a laptop and a signal. A sheet
> of paper goes in the tractor cab."

**🌾 It works for smallholders**
> "The Indian field we modelled is **2.5 hectares.** This isn't built for
> agribusiness."

---

## 4 · HOW HARD WE TRIED TO GET IT RIGHT

> "We didn't just build it. We spent most of our time trying to **prove
> ourselves wrong.**
>
> Our weather calculations are within **3%** of an independent scientific
> dataset.
>
> When our model says a crop struggled that year, the real government harvest
> records agree — **11 times out of 11.**
>
> We ran **eleven checks** on ourselves. Five passed. Four partly. **Two we
> failed** — and they're listed in the app, not hidden.
>
> We've found **eighteen mistakes in our own model**, every one of which was
> producing confident, believable, *wrong* answers before we caught it. They're
> all written down.
>
> And **827 automated tests** run every time we change a line."

**If they ask for the best example:**

> "Our flooded-rice model is what the entire India answer rests on, and we had
> no way to check it. So we found somewhere that floods rice exactly the same
> way and *does* publish the numbers — Arkansas.
>
> **We failed. We asked for nearly twice the water farmers actually use.**
>
> We rebuilt it, found three more mistakes, and it *still* fails. We could have
> fiddled a number until it matched. We didn't — because then the agreement
> would mean nothing."

---

## 5 · THE CLOSE (15 seconds)

> "So: any field on Earth. 176 ways of farming it, simulated through 41 years of
> real NASA weather. The ones that beat what you're doing now — with the proof
> attached, and an honest account of what we don't know.
>
> **FieldShift supports a decision. It doesn't make one.** Nothing here replaces
> the farmer's own judgement — and that sentence is on every single screen."

---

## QUICK ANSWERS

**"Which NASA data?"**
> "NASA POWER for the weather — 41 years of daily temperature, rain, humidity,
> wind and sunlight. NASADEM for the ground slope. And MODIS satellite imagery
> to check our ground-cover numbers. Every single number in the tool traces back
> to POWER."

**"Has a farmer used it?"**
> "No. Zero field trials. That's our biggest limitation and we say it first."

**"Does it work right now?"**
> "Pick a field on the map and watch."

**"What about cloud cover?"**
> "Our main data has no cloud gaps at all — it's not camera imagery. The
> satellite data we use for checking *does*, so we filter on the cloud flag.
> That matters: in a monsoon the cloud comes exactly when the crop is greenest."

**If the demo breaks:** open the printed field plan. It's paper. It can't crash.
