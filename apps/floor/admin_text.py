"""
Replace Django's cryptic admin words everywhere (all admin pages, all dropdowns):
  "---------" -> "— বাছুন (Select) —"     "Action:" -> "কাজ (Action):"
  "0 of 4 selected" -> "4টির মধ্যে 0টি বাছাই (0 of 4 selected)"
Called once from FloorConfig.ready().
"""
SELECT = "— বাছুন (Select) —"
CHOOSE_ACTION = "— একটি কাজ বাছুন (Choose an action) —"


def apply():
    from django.contrib.admin import helpers, options
    from django.db.models import fields
    from django.forms import models as form_models

    # 1. every blank choice in model choice lists (Django reuses this one list object everywhere)
    fields.BLANK_CHOICE_DASH[:] = [("", SELECT)]

    # 2. every foreign-key dropdown ("---------" is hard-coded as the default empty_label)
    if not getattr(form_models.ModelChoiceField, "_bn_patched", False):
        orig_init = form_models.ModelChoiceField.__init__

        def __init__(self, *args, **kwargs):
            orig_init(self, *args, **kwargs)
            if self.empty_label == "---------":
                self.empty_label = SELECT
        form_models.ModelChoiceField.__init__ = __init__
        form_models.ModelChoiceField._bn_patched = True

    # 3. the action bar on every list page
    helpers.ActionForm.base_fields["action"].label = "কাজ (Action):"
    if not getattr(options.ModelAdmin, "_bn_patched", False):
        orig_choices = options.ModelAdmin.get_action_choices
        orig_changelist = options.ModelAdmin.changelist_view

        def get_action_choices(self, request, default_choices=None):
            return orig_choices(self, request, default_choices=[("", CHOOSE_ACTION)])

        def changelist_view(self, request, extra_context=None):
            resp = orig_changelist(self, request, extra_context)
            ctx = getattr(resp, "context_data", None)
            if ctx and "cl" in ctx:
                n = len(ctx["cl"].result_list)
                ctx["selection_note"] = f"{n}টির মধ্যে 0টি বাছাই (0 of {n} selected)"
            return resp

        options.ModelAdmin.get_action_choices = get_action_choices
        options.ModelAdmin.changelist_view = changelist_view
        options.ModelAdmin._bn_patched = True
