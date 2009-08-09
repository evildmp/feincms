from django.contrib import admin
from django.contrib.admin.util import unquote
from django.http import Http404, HttpResponse, HttpResponseRedirect
from django.utils import simplejson
from django.utils.safestring import mark_safe
from django.utils.translation import ugettext_lazy as _

from feincms import settings
from feincms.admin.editor import django_boolean_icon


def _build_tree_structure(cls):
    """
    Build an in-memory representation of the page tree, trying to keep
    database accesses down to a minimum. The returned dictionary looks like
    this (as json dump):
    
        {"6": {"id": 6, "children": [7, 8, 10], "parent": null, "descendants": [7, 12, 13, 8, 10]},
         "7": {"id": 7, "children": [12], "parent": 6, "descendants": [12, 13]},
         "8": {"id": 8, "children": [], "parent": 6, "descendants": []},
         ...

    """
    all_nodes = { }
    def add_as_descendant(n, p):
        if not n: return
        all_nodes[n.id]['descendants'].append(p.id)
        add_as_descendant(n.parent, p)

    for p in cls.objects.order_by('tree_id', 'lft'):
        all_nodes[p.id] = { 'id': p.id, 'children' : [ ], 'descendants' : [ ], 'parent' : p.parent_id }
        if(p.parent_id):
            all_nodes[p.parent_id]['children'].append(p.id)
            add_as_descendant(p.parent, p)

    return all_nodes


# ------------------------------------------------------------------------
def ajax_editable_boolean_cell(item, attr, text='', override=None):
    """
    Generate a html snippet for showing a boolean value on the admin page.
    Item is an object, attr is the attribute name we should display. Text
    is an optional explanatory text to be included in the output.

    This function will emit code to produce a checkbox input with its state
    corresponding to the item.attr attribute if no override value is passed.
    This input is wired to run a JS ajax updater to toggle the value.

    If override is passed in, ignores the attr attribute and returns a
    static image for the override boolean with no user interaction possible
    (useful for "disabled and you can't change it" situations).
    """
    if text:
        text = '&nbsp;(%s)' % unicode(text)

    if override is not None:
        a = [ django_boolean_icon(override, text), text ]
    else:
        value = getattr(item, attr)
        a = [
              '<input type="checkbox"',
              value and ' checked="checked"' or '',
              ' onclick="return inplace_toggle_boolean(%d, \'%s\')";' % (item.id, attr),
              ' />',
              text,
            ]

    a.insert(0, '<div id="wrap_%s_%d">' % ( attr, item.id ))
    a.append('</div>')
    #print a
    return unicode(''.join(a))

# ------------------------------------------------------------------------
def ajax_editable_boolean(attr, short_description):
    """
    Convenience function: Assign the return value of this method to a variable
    of your ModelAdmin class and put the variable name into list_display.

    Example:
        class MyTreeEditor(TreeEditor):
            list_display = ('__unicode__', 'active_toggle')

            active_toggle = ajax_editable_boolean('active', _('is active'))
    """
    def _fn(self, item):
        return ajax_editable_boolean_cell(item, attr)
    _fn.allow_tags = True
    _fn.short_description = short_description
    _fn.editable_boolean_field = attr
    return _fn


class TreelistEditor(admin.ModelAdmin):
    class Media:
        css = {}
        js = []
        if settings.FEINCMS_ADMIN_MEDIA_HOTLINKING:
            js.extend(( "http://ajax.googleapis.com/ajax/libs/jquery/1.3.2/jquery.min.js", ))
        else:
            js.extend(( settings.FEINCMS_ADMIN_MEDIA + "jquery-1.3.2.min.js", ))

        js.extend(( settings.FEINCMS_ADMIN_MEDIA + "ie_compat.js",
                    settings.FEINCMS_ADMIN_MEDIA + "jquery.cookie.js" ,
                    settings.FEINCMS_ADMIN_MEDIA + "toolbox.js",
                    settings.FEINCMS_ADMIN_MEDIA + "page_toolbox.js",
                    ))

    def __init__(self, *args, **kwargs):
        super(TreelistEditor, self).__init__(*args, **kwargs)

        if 'indented_short_title' not in self.list_display:
            if self.list_display[0] == 'action_checkbox':
                self.list_display[1] = 'indented_short_title'
            else:
                self.list_display[0] = 'indented_short_title'
        self.list_display_links = ('indented_short_title',)

        opts = self.model._meta
        self.change_list_template = [
            'admin/feincms/%s/%s/treelist_editor.html' % (opts.app_label, opts.object_name.lower()),
            'admin/feincms/%s/treelist_editor.html' % opts.app_label,
            'admin/feincms/treelist_editor.html',
            ]

    def indented_short_title(self, item):
        """
        Generate a short title for a page, indent it depending on
        the page's depth in the hierarchy.
        """
        r = '''<span onclick="return page_tree_handler('%d')" id="page_marker-%d"
            class="page_marker" style="width: %dpx;">&nbsp;</span>&nbsp;''' % (
                item.id, item.id, 14+item.level*14)

        if hasattr(item, 'short_title'):
            return mark_safe(r + item.short_title())
        return mark_safe(r + unicode(item))
    indented_short_title.short_description = _('title')
    indented_short_title.allow_tags = True

    def _collect_editable_booleans(self):
        """
        Collect all fields marked as editable booleans. We do not
        want the user to be able to edit arbitrary fields by crafting
        an AJAX request by hand.
        """
        if hasattr(self, '_ajax_editable_booleans'):
            return

        self._ajax_editable_booleans = {}

        for field in self.list_display:
            # The ajax_editable_boolean return value has to be assigned
            # to the ModelAdmin class
            item = getattr(self.__class__, field, None)
            if not item:
                continue

            attr = getattr(item, 'editable_boolean_field', None)
            if attr:
                result_func = getattr(item, 'editable_boolean_result',
                                      lambda self, page: [ ajax_editable_boolean_cell(page, attr) ])
                self._ajax_editable_booleans[attr] = result_func

    def _toggle_boolean(self, request):
        """
        Handle an AJAX toggle_boolean request
        """
        self._collect_editable_booleans()

        item_id = request.POST.get('item_id')
        attr = request.POST.get('attr')

        if not self._ajax_editable_booleans.has_key(attr):
            return HttpResponseBadRequest("not a valid attribute %s" % attr)

        try:
            obj = self.model._default_manager.get(pk=unquote(item_id))

            before_data = self._ajax_editable_booleans[attr](self, obj)

            setattr(obj, attr, not getattr(obj, attr))
            obj.save()
            self.refresh_visible_pages()    # ???: Perhaps better a post_save signal?

            # Construct html snippets to send back to client for status update
            data = self._ajax_editable_booleans[attr](self, obj)

        except Exception, e:
            return HttpResponse("FAILED " + unicode(e), mimetype="text/plain")

        # Weed out unchanged cells to keep the updates small. This assumes
        # that the order a possible get_descendents() returns does not change
        # before and after toggling this attribute. Unlikely, but still...
        d = []
        for a, b in zip(before_data, data):
            if a != b:
                d.append(b)

        return HttpResponse(simplejson.dumps(d), mimetype="application/json")

    def changelist_view(self, request, extra_context=None, *args, **kwargs):
        """
        Handle the changelist view, the django view for the model instances
        change list/actions page.
        """

        if 'actions_column' not in self.list_display:
            self.list_display.append('actions_column')

        # handle common AJAX requests
        if request.is_ajax():
            cmd = request.POST.get('__cmd')
            if cmd == 'toggle_boolean':
                return self._toggle_boolean(request)
            elif cmd == 'move_node':
                return self._move_node(request)

        extra_context = extra_context or {}
        extra_context['FEINCMS_ADMIN_MEDIA'] = settings.FEINCMS_ADMIN_MEDIA
        extra_context['tree_structure'] = mark_safe(simplejson.dumps(
                                                    _build_tree_structure(self.model)))

        return super(TreelistEditor, self).changelist_view(request, extra_context, *args, **kwargs)

    def _move_node(self, request):
        cut_item = self.model._tree_manager.get(pk=request.POST.get('cut_item'))
        pasted_on = self.model._tree_manager.get(pk=request.POST.get('pasted_on'))
        position = request.POST.get('position')

        if position in ('last-child', 'left'):
            self.model._tree_manager.move_node(cut_item, pasted_on, position)

            # Ensure that model save has been run
            source = self.model._tree_manager.get(pk=request.POST.get('cut_item'))
            source.save()

            return HttpResponse('OK')
        return HttpResponse('FAIL')

    def queryset(self, request):
        """
        Returns a QuerySet of all model instances that can be edited by the
        admin site. This is used by changelist_view.
        """
        
        # Use default ordering, always
        return self.model._default_manager.get_query_set()

    def _actions_column(self, page):
        actions = []
        actions.append(u'<a href="#" onclick="return cut_item(\'%s\', this)" title="%s">&#x2702;</a>' % (
            page.pk, _('Cut')))

        actions.append(u'<a class="paste_target" href="#" onclick="return paste_item(\'%s\', \'last-child\')" title="%s">&#x21b3;</a>' % (
            page.pk, _('Insert as child')))
        actions.append(u'<a class="paste_target" href="#" onclick="return paste_item(\'%s\', \'left\')" title="%s">&#x21b1;</a>' % (
            page.pk, _('Insert before')))
        return actions

    def actions_column(self, page):
        return u' '.join(self._actions_column(page))
    actions_column.allow_tags = True
    actions_column.short_description = _('actions')