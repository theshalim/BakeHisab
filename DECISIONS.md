# Decisions Addendum — v1.0 (BINDING)

> এই file-টি মূল Blueprint ("Simple Bakery Production & Cost Calculator")-এর ওপরে প্রাধান্য পাবে।
> Blueprint আর এই file-এর মধ্যে অমিল হলে **এই file মানতে হবে**।
> যে কেউ (মানুষ বা AI) code লেখার আগে এটি পড়বেন। Rule বদলাতে হলে আগে এখানে লিখুন, তারপর code ও test বদলান।

Approved by owner: 2026-11-07

---

## D-01 · LPG ও Packaging — double counting নয়
- LPG এবং Packaging **শুধু Recipe/BOM-এর line item** হিসেবে থাকবে (Raw Material Master-এ দাম সহ)।
- **Standard Cost-এ থাকবে শুধু:** Labour, Electricity, Other।
- Code: `apps/costing/engine.py` → `STANDARD_COMPONENTS = ("labour", "electricity", "other")`

## D-02 · Actual consumption product-wise ভাগ
- Actual consumption নেওয়া হবে **পুরো plan-এর মোট** হিসেবে (যেমন Flour মোট 510 KG)।
- প্রতিটি material **আলাদাভাবে** ভাগ হবে, **ওই material-এর product-wise estimated quantity অনুপাতে**:
  `Line-এর Actual = মোট Actual × (Line-এর Estimated ঐ material ÷ মোট Estimated ঐ material)`
- যে material শুধু একটি product-এ লাগে (যেমন Egg শুধু Cake-এ), তার পুরো actual ওই product-এ যাবে।
- ❌ Planned quantity অনুপাতে ভাগ করা **নিষিদ্ধ** (ভুল ফল দেয়)।
- ভাগের অংশগুলো যোগ করলে সবসময় হুবহু মোট আসবে (শেষ line বাকিটা পায়)।

## D-03 · Product-wise Good / Rejected
- প্রতিটি plan line-এ আলাদা **Produced** ও **Rejected** quantity। `Good = Produced − Rejected`।
- Rejected > Produced হলে error।

## D-04 · Unit conversion
- Conversion **শুধু একই ধরনের unit-এর মধ্যে**: KG↔GM, L↔ML, PCS।
- **KG↔Litre conversion নেই** (v1-এ density নেই)। Recipe line-এর unit material-এর unit-এর সাথে না মিললে save হবে না।
- Recipe-তে GM/ML লেখা যাবে; হিসাবের আগে material-এর নিজের unit-এ convert হবে (150 GM → 0.15 KG)।

## D-05 · Fractional batch ও Standard Cost
- `Batches = Planned Qty ÷ Batch Yield` — **ভগ্নাংশ থাকবে, round up হবে না** (550/100 = 5.5)।
- Labour / Electricity / Other cost **প্রতি batch** হিসেবে Recipe-তে সেট থাকে → `Batches × per-batch cost`।
- Actual Labour / Electricity / Other পুরো plan-এর মোট হিসেবে এন্ট্রি হয় → প্রতিটি component, line-এর **estimated ঐ component** অনুপাতে ভাগ হয় (D-02-এর পদ্ধতি)।
- **Estimated cost per piece** = Line estimated total ÷ **Planned Qty** (v1-এ standard wastage নেই)।
- **Actual cost per piece** = Line actual total ÷ **Good Qty** (BR-10)। Good Qty = 0 হলে per piece দেখানো হবে না।

## D-06 · Recipe ও Price snapshot
- **Draft** অবস্থায় হিসাব সবসময় **আজকের active recipe ও price** দিয়ে হয়।
- **Draft → In Production** হওয়ার মুহূর্তে server recipe (version, yield, per-batch quantity, per-batch standard cost) ও price-এর **snapshot lock করে**।
- এরপর দাম বা recipe বদলালেও ঐ plan-এর হিসাব বদলাবে না (BR-08, BR-14)।
- *(Derived)* **Actual cost-ও locked snapshot price দিয়ে হিসাব হয়** — তাই variance শুধু "কত বেশি/কম ব্যবহার হলো" দেখায়, দামের ওঠানামা নয়।

## D-07 · Production ID
- Phone প্রতিটি plan-এ একটি **Local UUID** দেয় (offline-এও)। একই UUID বারবার sync হলে **নতুন plan তৈরি হয় না**, আগেরটাই ফেরত আসে (BR-13)।
- `PRD-YYYYMMDD-NNN` নম্বর **শুধু server দেয়**। Sequence **factory অনুযায়ী আলাদা**, তারিখ = **Production Date**।

## D-08 · Source of truth
- Server-এর (Django) calculation engine-ই চূড়ান্ত। Mobile app-এর হিসাব শুধু preview।
- Flutter-এ engine port করলে `apps/costing/tests.py`-এর একই test সংখ্যা সেখানেও pass করতে হবে।

## D-09 · Status ও edit rule
`Draft → In Production → Completed`

| Status | Plan (product, quantity, date) | Actual (consumption, cost, produced/rejected) |
|---|---|---|
| Draft | Edit করা যায় | এন্ট্রি করা যায় না |
| In Production | **Locked** | Edit করা যায় (আংশিক save চলবে) |
| Completed | Locked | শুধু **Super Admin correction**, কারণ লিখতে হবে, **Audit Log**-এ before/after থাকবে |

- *(Derived)* Complete করার আগে সব material-এর actual, Labour/Electricity/Other actual (খরচ না থাকলে **0** লিখতে হবে) এবং প্রতিটি line-এর Produced/Rejected দিতে হবে — না দিলে Complete আটকে যাবে।
- *(Derived)* যে material plan-এর কোনো recipe-তে নেই তার actual এন্ট্রি করা যাবে না (ভাগ করার ভিত্তি নেই)।

## D-10 · Roles
- Super Admin = Django superuser।
- Production Manager ও Production Operator = **Production User** — plan তৈরি, production শুরু, actual এন্ট্রি, complete।
- Factory Admin — production user যা পারে + master data।
- Accounts/Cost Manager ও Management — web portal, report দেখা/export। Plan তৈরি করতে পারেন না।
- প্রত্যেকে শুধু নিজের factory-র data দেখেন (BR-12)।

## D-11 · Rounding ও precision
- সব হিসাব Python `Decimal`-এ; **মাঝের কোনো ধাপে round হয় না**।
- Database-এ quantity ও টাকা **4 decimal** পর্যন্ত রাখা হয়।
- **Round শুধু দেখানোর সময়:**
  - টাকা → 2 decimal (half-up)
  - PCS জাতীয় requirement → **সবসময় round up** (2050.5 → 2051)
  - KG / Litre → 3 decimal (গ্রাম / ml পর্যন্ত)

## Edge rules (derived — owner চাইলে বদলানো যাবে)
- কোনো component-এর estimated মোট 0 হলে (যেমন recipe-তে labour 0 কিন্তু actual labour আছে) সেটা **planned qty অনুপাতে** ভাগ হবে।
- Standard cost Recipe-র সাথে থাকে, তাই নতুন labour rate মানে নতুন Recipe version।
