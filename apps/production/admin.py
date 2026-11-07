from django import forms
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.utils.html import format_html_join, format_html
from django.utils.safestring import mark_safe

from apps.costing import engine as E
from apps.costing.engine import CalculationError
from apps.factories.admin import FactoryScopedAdmin

from . import services as S
from .models import ActualMaterial, AuditLog, ProductionLine, ProductionPlan, Status


class LineForm(forms.ModelForm):
    class Meta:
        model = ProductionLine
        fields = ("variant", "planned_qty", "produced_qty", "rejected_qty")

    def clean(self):
        c = super().clean()
        p, r = c.get("produced_qty"), c.get("rejected_qty")
        if p is not None and r is not None and r > p:
            raise forms.ValidationError("Rejected, Produced-এর চেয়ে বেশি হতে পারবে না।")
        return c


class LineInline(admin.TabularInline):
    model = ProductionLine
    form = LineForm
    extra = 0

    def get_readonly_fields(self, request, obj=None):
        if obj is None or obj.status == Status.DRAFT:
            return ("produced_qty", "rejected_qty", "recipe_version")
        if obj.status == Status.IN_PRODUCTION:
            return ("variant", "planned_qty", "recipe_version")
        return ("variant", "planned_qty", "produced_qty", "rejected_qty", "recipe_version")

    def has_add_permission(self, request, obj=None):
        return obj is None or obj.status == Status.DRAFT

    def has_delete_permission(self, request, obj=None):
        return obj is None or obj.status == Status.DRAFT


class ActualMaterialInline(admin.TabularInline):
    model = ActualMaterial
    extra = 0
    verbose_name_plural = "Actual material consumption (total for the whole plan)"

    def has_add_permission(self, request, obj=None):
        return obj is not None and obj.status == Status.IN_PRODUCTION

    def has_change_permission(self, request, obj=None):
        return obj is not None and obj.status == Status.IN_PRODUCTION

    def has_delete_permission(self, request, obj=None):
        return obj is not None and obj.status == Status.IN_PRODUCTION

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "material":
            obj_id = request.resolver_match.kwargs.get("object_id")
            from apps.catalog.models import RawMaterial
            kwargs["queryset"] = RawMaterial.objects.filter(
                planpricesnapshot__plan_id=obj_id) if obj_id else RawMaterial.objects.none()
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


@admin.register(ProductionPlan)
class ProductionPlanAdmin(FactoryScopedAdmin):
    list_display = ("__str__", "factory", "production_date", "status", "created_by")
    list_filter = ("status", "factory", "production_date")
    date_hierarchy = "production_date"
    inlines = [LineInline, ActualMaterialInline]
    actions = ["action_start", "action_complete"]

    def get_fields(self, request, obj=None):
        base = ["factory", "production_date"]
        if obj is None:
            return base
        fields = ["plan_no", "status"] + base + ["calculation"]
        if obj.status != Status.DRAFT:
            fields += ["actual_labour", "actual_electricity", "actual_other"]
        return fields

    def get_readonly_fields(self, request, obj=None):
        if obj is None:
            return ()
        ro = ["plan_no", "status", "calculation", "factory"]
        if obj.status != Status.DRAFT:
            ro.append("production_date")
        if obj.status == Status.COMPLETED:
            ro += ["actual_labour", "actual_electricity", "actual_other"]
        return ro

    def has_delete_permission(self, request, obj=None):
        return obj is None or obj.status == Status.DRAFT

    @transaction.atomic
    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
            S._assign_plan_no(obj)
        super().save_model(request, obj, form, change)

    # ---- read-only calculation panel ------------------------------------
    @admin.display(description="Calculation")
    def calculation(self, obj):
        if not obj.pk or not obj.lines.exists():
            return "Product যোগ করে Save করুন।"
        try:
            est, inp = S.estimate_plan(obj)
        except CalculationError as e:
            return format_html('<b style="color:#b00">{}</b>', e)
        info = inp.material_info
        mat_rows = format_html_join("", "<tr><td>{}</td><td style='text-align:right'>{}</td><td>{}</td>"
                                    "<td style='text-align:right'>{}</td></tr>", (
            (info[m][0], E.display_qty(q, info[m][2]), info[m][1], E.money(est.material_costs[m]))
            for m, q in est.materials.items()))
        names = {ln.id: str(ln.variant) for ln in obj.lines.all()}
        act = cmp = None
        note = ""
        if obj.status != Status.DRAFT:
            try:
                _, act, _ = S.actual_plan(obj)
                cmp = E.compare(est, act)
            except CalculationError as e:
                note = format_html('<p style="color:#a60">Actual এখনো অসম্পূর্ণ: {}</p>', e)
        line_rows = format_html_join("", "<tr><td>{}</td><td style='text-align:right'>{}</td>"
                                     "<td style='text-align:right'>{}</td><td style='text-align:right'>{}</td>"
                                     "<td style='text-align:right'>{}</td><td style='text-align:right'>{}</td></tr>", (
            (names[ln.line_key], E.money(ln.per_piece_qty), E.money(ln.total_cost), E.money(ln.cost_per_piece),
             E.money(act.line(ln.line_key).total_cost) if act else "–",
             E.money(act.line(ln.line_key).cost_per_piece) if act else "–")
            for ln in est.lines))
        total = format_html(
            "<p><b>Estimated total: {} Tk</b> (material {} + labour {} + electricity {} + other {}) · "
            "per piece {} Tk</p>",
            E.money(est.total_cost), E.money(est.total_material_cost), E.money(est.standard["labour"]),
            E.money(est.standard["electricity"]), E.money(est.standard["other"]), E.money(est.cost_per_piece))
        if cmp:
            vrows = format_html_join("", "<tr><td>{}</td><td style='text-align:right'>{}</td>"
                                     "<td style='text-align:right'>{}</td><td style='text-align:right;color:{}'>{} ({}%)</td></tr>", (
                (r["label"], E.money(r["v"].estimated), E.money(r["v"].actual),
                 "#b00" if r["v"].amount > 0 else "#070", E.money(r["v"].amount),
                 E.percent(r["v"].percent) if r["v"].percent is not None else "–")
                for r in S.variance_rows(est, act)))
            total += format_html("<table><tr><th>Variance</th><th>Estimated</th><th>Actual</th>"
                                 "<th>Difference</th></tr>{}</table><br>", vrows)
        if obj.status != Status.DRAFT:
            warns = S.actual_warnings(obj)
            if warns:
                total += format_html('<div style="background:#fff6dd;padding:8px;border-left:4px solid #e0a800">'
                                     "<b>⚠ যাচাই করুন:</b><ul>{}</ul></div>",
                                     format_html_join("", "<li>{}</li>", ((w,) for w in warns)))
        basis = "current recipe & price (Draft)" if obj.status == Status.DRAFT else "locked snapshot"
        return format_html(
            "<p><i>Based on {}</i></p>{}{}"
            "<table><tr><th>Raw material</th><th>Required</th><th>Unit</th><th>Est. cost</th></tr>{}</table><br>"
            "<table><tr><th>Product</th><th>Planned</th><th>Est. cost</th><th>Est./pc</th>"
            "<th>Actual cost</th><th>Actual/pc (good)</th></tr>{}</table>",
            basis, total, note, mat_rows, line_rows)

    # ---- status actions -----------------------------------------------
    def _run(self, request, queryset, fn, label):
        for plan in queryset:
            try:
                fn(user=request.user, plan=plan)
                self.message_user(request, f"{plan}: {label}", messages.SUCCESS)
            except (CalculationError, S.StateError, PermissionDenied) as e:
                self.message_user(request, f"{plan}: {e}", messages.ERROR)

    @admin.action(description="উৎপাদন শুরু — দাম lock (Start Production)")
    def action_start(self, request, queryset):
        self._run(request, queryset, S.start_production, "In Production")

    @admin.action(description="উৎপাদন শেষ (Complete Production)")
    def action_complete(self, request, queryset):
        self._run(request, queryset, S.complete_production, "Completed")


@admin.register(AuditLog)
class AuditLogAdmin(FactoryScopedAdmin):
    factory_path = "plan__factory"
    list_display = ("created_at", "action", "plan", "user", "reason")
    list_filter = ("action",)
    readonly_fields = ("created_at", "action", "plan", "user", "reason", "changes")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
