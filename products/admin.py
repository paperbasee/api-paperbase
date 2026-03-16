from django.contrib import admin
from django import forms
from django.utils.html import mark_safe

from .models import Brand, Category, NavbarCategory, Product, ProductImage


class ProductImageInline(admin.TabularInline):
    model = ProductImage
    extra = 0


class ProductAdminForm(forms.ModelForm):
    class Meta:
        model = Product
        fields = '__all__'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['category'].queryset = NavbarCategory.objects.filter(is_active=True)
        self.fields['sub_category'].queryset = Category.objects.filter(is_active=True).select_related('navbar_category')
        self.fields['sub_category'].required = False


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    form = ProductAdminForm
    list_display = ['name', 'brand', 'get_category', 'get_sub_category', 'price', 'stock', 'badge', 'is_featured', 'is_active']
    list_editable = ['stock', 'is_active']
    list_filter = ['category', 'sub_category', 'badge', 'is_featured', 'is_active']
    search_fields = ['name', 'brand']
    prepopulated_fields = {'slug': ('name',)}
    inlines = [ProductImageInline]
    autocomplete_fields = ['category', 'sub_category']
    fieldsets = (
        (None, {
            'fields': ('name', 'brand', 'slug', 'category', 'sub_category')
        }),
        ('Pricing', {
            'fields': ('price', 'original_price', 'badge')
        }),
        ('Media', {
            'fields': ('image',)
        }),
        ('Additional Information', {
            'fields': ('description', 'stock', 'is_featured', 'is_active')
        }),
    )

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        field = super().formfield_for_dbfield(db_field, request, **kwargs)
        if db_field.name == 'stock' and field:
            field.widget.attrs.update({'style': 'width: 5rem;'})
        return field

    def get_category(self, obj):
        return obj.category.name if obj.category else '-'
    get_category.short_description = 'Category'
    get_category.admin_order_field = 'category__name'

    def get_sub_category(self, obj):
        return obj.sub_category.name if obj.sub_category else '-'
    get_sub_category.short_description = 'Subcategory'
    get_sub_category.admin_order_field = 'sub_category__name'


@admin.register(NavbarCategory)
class NavbarCategoryAdmin(admin.ModelAdmin):
    list_display = ['name', 'slug', 'order', 'is_active', 'subcategory_count']
    list_editable = ['order', 'is_active']
    search_fields = ['name', 'slug']
    prepopulated_fields = {'slug': ('name',)}
    ordering = ['order', 'name']

    def subcategory_count(self, obj):
        count = obj.subcategories.count()
        return f"{count} subcategories"
    subcategory_count.short_description = 'Subcategories'


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ['name', 'slug', 'navbar_category', 'order', 'is_active', 'product_count']
    list_filter = ['navbar_category', 'is_active']
    list_editable = ['order', 'is_active']
    search_fields = ['name', 'slug']
    prepopulated_fields = {'slug': ('name',)}
    ordering = ['navbar_category__name', 'order', 'name']
    autocomplete_fields = ['navbar_category']
    readonly_fields = ['image_preview']
    fieldsets = (
        (None, {
            'fields': ('name', 'slug', 'navbar_category', 'description')
        }),
        ('Media', {
            'fields': ('image', 'image_preview'),
            'description': 'This image is displayed in the mobile hamburger navigation menu.',
        }),
        ('Display', {
            'fields': ('order', 'is_active')
        }),
    )

    def image_preview(self, obj):
        if obj.image:
            return mark_safe(f'<img src="{obj.image.url}" style="max-height:120px;" />')
        return '(no image uploaded)'
    image_preview.short_description = 'Current Image'

    def product_count(self, obj):
        count = obj.subcategory_products.count()
        return f"{count} products"
    product_count.short_description = 'Products'

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('navbar_category')


@admin.register(Brand)
class BrandAdmin(admin.ModelAdmin):
    list_display = ['name', 'brand_type', 'order', 'is_active', 'redirect_url_preview', 'created_at']
    list_filter = ['brand_type', 'is_active']
    list_editable = ['order', 'is_active']
    search_fields = ['name', 'slug']
    prepopulated_fields = {'slug': ('name',)}
    ordering = ['brand_type', 'order', 'name']
    readonly_fields = ['created_at', 'updated_at']
    fieldsets = (
        (None, {
            'fields': ('name', 'slug', 'image')
        }),
        ('Configuration', {
            'fields': ('brand_type', 'redirect_url', 'order', 'is_active')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )

    def redirect_url_preview(self, obj):
        url = obj.redirect_url
        if len(url) > 40:
            return f"{url[:40]}..."
        return url
    redirect_url_preview.short_description = 'Redirect URL'
