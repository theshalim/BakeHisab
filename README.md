# বেকহিসাব (BakeHisab) — Bakery Production & Cost Calculator

Django backend + Web Admin Portal (Django Admin)। **আগে [DECISIONS.md](DECISIONS.md) পড়ুন।**

## কী তৈরি হয়েছে
| অংশ | কোথায় |
|---|---|
| Calculation engine (pure Python, DB ছাড়া) | `apps/costing/engine.py` |
| Engine test (হাতে কষা সংখ্যা) | `apps/costing/tests.py` |
| Factory, User role | `apps/factories/` |
| Unit, Raw Material, Price history, Product, Variant, Recipe/BOM | `apps/catalog/` |
| Production plan, snapshot, actual, audit log, workflow | `apps/production/` |
| Business rule-এর একমাত্র জায়গা | `apps/production/services.py` |
| Sample data (আসল নয়) | `apps/catalog/sample_data.py` |

**এখনো বাকি:** REST API (Step 2), Flutter app (Step 3), offline sync (Step 4), PDF/Excel export, PostgreSQL (এখন SQLite)।

## চালানোর নিয়ম (Windows)
```powershell
cd C:\Users\Administrator\bakery-calc
.venv\Scripts\python manage.py migrate
.venv\Scripts\python manage.py seed_sample        # sample factory/product/recipe
.venv\Scripts\python manage.py createsuperuser    # একবারই
.venv\Scripts\python manage.py runserver
```
Browser-এ খুলুন: http://127.0.0.1:8000/admin/

নতুন machine-এ প্রথমবার: `python -m venv .venv` তারপর `.venv\Scripts\pip install -r requirements.txt`।

## Test চালানো
```powershell
.venv\Scripts\python manage.py test apps
```

## দুইটা আলাদা screen
| কে | কোথায় | কাজ |
|---|---|---|
| Factory-র লোক (Production Manager/Operator) | **http://127.0.0.1:8000/** | শুধু ৩ ধাপ: পরিমাণ লিখুন → শুরু করুন → শেষ করুন |
| Admin (setup) | http://127.0.0.1:8000/admin/ | Product, recipe, দাম, user সেট করা |

Floor screen-এর code: `apps/floor/`। Flutter app বানালে এই একই ৩-ধাপের flow রাখতে হবে।

## Admin-এ একটা plan চালিয়ে দেখা (পুরনো, শুধু Admin-এর জন্য)
1. **Production plans → Add** → Factory A, তারিখ, নিচে product ও quantity → Save।
2. Save-এর পর **Calculation** অংশে raw material requirement, estimated cost, per piece দেখাবে।
3. List page-এ plan select করুন → Action **"Start production"** → recipe ও দাম lock হবে।
4. Plan খুলে actual Labour/Electricity/Other, প্রতিটি product-এর Produced/Rejected, আর নিচে প্রতিটি material-এর actual consumption দিন → Save।
5. Action **"Complete production"** → Calculation অংশে actual cost ও variance দেখাবে।
