from django.core.management.base import BaseCommand

from apps.catalog.bakery_catalog import load_bakery_catalog


class Command(BaseCommand):
    help = "Load the SAMPLE bakery catalog: units, categories, materials, prices, products, recipes (safe to run twice)."

    def handle(self, *args, **opts):
        factory, mats, variants = load_bakery_catalog()
        self.stdout.write(self.style.SUCCESS(
            f"{factory}: {len(mats)} materials, {len(variants)} product variants loaded."))
