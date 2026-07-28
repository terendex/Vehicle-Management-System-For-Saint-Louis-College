"""Opt-in pagination.

The frontend consumes list endpoints as plain JSON arrays (`res.data.map(...)`),
so switching DRF's default pagination on globally would change every response
into `{count, next, previous, results}` and break every table in the UI at once.

This class keeps the array shape by default and paginates only when the caller
explicitly asks — `?page=1` or `?page_size=25`. That makes a bounded, flat-cost
response available to any screen that wants it without a big-bang migration:
move one page at a time, reading `res.data.results` once it sends `?page=`.
"""
from rest_framework.pagination import PageNumberPagination


class DefaultPagination(PageNumberPagination):
    page_size_query_param = 'page_size'
    max_page_size = 500

    def paginate_queryset(self, queryset, request, view=None):
        # No explicit request to paginate → return None, which tells DRF to
        # serialise the queryset as a bare list exactly as it does today.
        if self.page_query_param not in request.query_params and \
           self.page_size_query_param not in request.query_params:
            return None
        return super().paginate_queryset(queryset, request, view)
