"""Types (Master data → Types): the kinds of products in the price list, as a tree. A revenue item gets the type
of the product picked for it."""
from ..models import CashflowEntry, PriceItem, ProductType
from .trees import TreeList

LIST = TreeList(ProductType, "product_type", "type", "sub-types",
                [(PriceItem.product_type_id, "products"), (CashflowEntry.product_type_id, "items")])
tree_order, label, offered, get = LIST.tree_order, LIST.label, LIST.offered, LIST.get
add, update, set_active, usage, delete = LIST.add, LIST.update, LIST.set_active, LIST.usage, LIST.delete
