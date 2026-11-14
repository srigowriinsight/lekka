"""Categories (Master data → Categories): optional on any expense or revenue item, as a tree."""
from ..models import CashflowEntry, Category
from .trees import TreeList

LIST = TreeList(Category, "category", "category", "sub-categories", [(CashflowEntry.category_id, "items")])
tree_order, label, offered, get = LIST.tree_order, LIST.label, LIST.offered, LIST.get
add, update, set_active, usage, delete = LIST.add, LIST.update, LIST.set_active, LIST.usage, LIST.delete
