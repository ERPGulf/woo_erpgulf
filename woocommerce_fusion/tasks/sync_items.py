import json
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional, Tuple
import re  

import frappe
from erpnext.stock.doctype.item.item import Item
from frappe import ValidationError, _, _dict
from frappe.query_builder import Criterion
from frappe.utils import get_datetime, now
from jsonpath_ng.ext import parse

from woocommerce_fusion.exceptions import SyncDisabledError
from woocommerce_fusion.tasks.sync import SynchroniseWooCommerce
from woocommerce_fusion.woocommerce.doctype.woocommerce_product.woocommerce_product import (
    WooCommerceProduct,
)
from woocommerce_fusion.woocommerce.doctype.woocommerce_server.woocommerce_server import (
    WooCommerceServer,
)
from woocommerce_fusion.woocommerce.woocommerce_api import (
    generate_woocommerce_record_name_from_domain_and_id,
)
from woocommerce import API



def run_item_sync_from_hook(doc, method):
    """
    Intended to be triggered by a Document Controller hook from Item
    """
    if (
        doc.doctype == "Item"
        and not doc.flags.get("created_by_sync", None)
        and len(doc.woocommerce_servers) > 0
    ):
        frappe.msgprint(
            _("Background sync to WooCommerce triggered for {0} {1}").format(frappe.bold(doc.name), method),
            indicator="blue",
            alert=True,
        )
        frappe.enqueue(clear_sync_hash_and_run_item_sync, item_code=doc.name)


@frappe.whitelist()
def run_item_sync(
    item_code: Optional[str] = None,
    item: Optional[Item] = None,
    woocommerce_product_name: Optional[str] = None,
    woocommerce_product: Optional[WooCommerceProduct] = None,
    enqueue=False,
) -> Tuple[Item, WooCommerceProduct]:
    """
    Helper funtion that prepares arguments for item sync
    """
    # Validate inputs, at least one of the parameters should be provided
    frappe.log_error(
        title="run_item_sync Debug",
        message=frappe.as_json({
            "item_code": item_code,
            "item": item.as_dict() if item else None,
            "woocommerce_product_name": woocommerce_product_name,
            "woocommerce_product": woocommerce_product.as_dict() if woocommerce_product else None,
            "enqueue": enqueue,
        })
    )
 
    if not any([item_code, item, woocommerce_product_name, woocommerce_product]):
        raise ValueError(
            (
                "At least one of item_code, item, woocommerce_product_name, woocommerce_product parameters required"
            )
        )

    # Get ERPNext Item and WooCommerce product if they exist
    if woocommerce_product or woocommerce_product_name:
        frappe.log_error("64")
        if not woocommerce_product:
            frappe.log_error("66")
            woocommerce_product = frappe.get_doc(
                {"doctype": "WooCommerce Product", "name": woocommerce_product_name}
            )
            woocommerce_product.load_from_db()

        # Trigger sync
        frappe.log_error("73")
        sync = SynchroniseItem(woocommerce_product=woocommerce_product)
        if enqueue:
            frappe.enqueue(sync.run)
        else:
            sync.run()

    elif item or item_code:
        frappe.log_error("92")
        if not item:
            item = frappe.get_doc("Item", item_code)
            
        # 🧠 --- VALIDATION BEFORE SYNC ---
        # Check price
        price_doc = frappe.get_all(
            "Item Price",
            filters={"item_code": item.item_code, "price_list": "Standard Selling"},
            fields=["price_list_rate"],
            limit=1
        )
        price = price_doc[0].price_list_rate if price_doc else 0.0

        if item.custom_disable_sync == 1 or item.custom_disable_sync_if_not_in_stock == 1:
            frappe.log_error(
                "❌ Skipped Item Sync","sync disabled"
            )
            return (None, None)
                   
        if not price or price <= 0:
            frappe.log_error(
                "❌ Skipped Item Sync",
                f"Item {item.item_code} not synced — Price: {price}"
            )
            return (None, None)
        
        # 🧠 --- END VALIDATION ---
        if not item.woocommerce_servers:
            frappe.throw(_("No WooCommerce Servers defined for Item {0}").format(item_code))
        for wc_server in item.woocommerce_servers:
            # Trigger sync for every linked server
            sync = SynchroniseItem(
                item=ERPNextItemToSync(item=item, item_woocommerce_server_idx=wc_server.idx)
            )
            if enqueue:
                frappe.enqueue(sync.run)
            else:
                sync.run()

    return (
        sync.item.item if sync and sync.item else None,
        sync.woocommerce_product if sync else None,
    )


def sync_woocommerce_products_modified_since(date_time_from=None):
    """
    Get list of WooCommerce products modified since date_time_from
    """
    wc_settings = frappe.get_doc("WooCommerce Integration Settings")

    if not date_time_from:
        date_time_from = wc_settings.wc_last_sync_date_items

    # Validate
    if not date_time_from:
        error_text = _(
            "'Last Items Syncronisation Date' field on 'WooCommerce Integration Settings' is missing"
        )
        frappe.log_error(
            "WooCommerce Items Sync Task Error",
            error_text,
        )
        raise ValueError(error_text)

    wc_products = get_list_of_wc_products(date_time_from=date_time_from)
    for wc_product in wc_products:
        try:
            run_item_sync(woocommerce_product=wc_product, enqueue=True)
        # Skip items with errors, as these exceptions will be logged
        except Exception:
            pass

    frappe.db.set_single_value("WooCommerce Settings", "wc_last_sync_date_items", now())


@dataclass
class ERPNextItemToSync:
    """Class for keeping track of an ERPNext Item and the relevant WooCommerce Server to sync to"""

    item: Item
    item_woocommerce_server_idx: int

    @property
    def item_woocommerce_server(self):
        return self.item.woocommerce_servers[self.item_woocommerce_server_idx - 1]

class SynchroniseItem(SynchroniseWooCommerce):
    """
    Class for managing synchronisation of WooCommerce Product with ERPNext Item
    """

    def __init__(
        self,
        servers: list = None,
        item: Optional[ERPNextItemToSync] = None,
        woocommerce_product: Optional[WooCommerceProduct] = None,
    ) -> None:
        super().__init__(servers)
        self.item = item
        self.woocommerce_product = woocommerce_product
        self.settings = frappe.get_cached_doc("WooCommerce Integration Settings")
        if not servers:
            servers = frappe.get_all(
                "WooCommerce Server",
                fields=["woocommerce_server_url", "api_consumer_key", "api_consumer_secret", "enable_sync"],
                filters={"enable_sync": 1},
                limit=1
            )
        if servers and len(servers) > 0:
            server = servers[0] if isinstance(servers[0], dict) else servers[0].as_dict()
            self.wcapi = API(url=server.get("woocommerce_server_url").rstrip('/'),consumer_key=server.get("api_consumer_key"),consumer_secret=server.get("api_consumer_secret"),version="wc/v3")
            self.consumer_key = server.get("api_consumer_key")
            self.consumer_secret = server.get("api_consumer_secret")
        else:
            self.wcapi = None

    def run(self):
        """
        Run synchronisation
        """
        try:
            self.get_corresponding_item_or_product()
            self.sync_wc_product_with_erpnext_item()
        except Exception as err:
            try:
                woocommerce_product_dict = (
                    self.woocommerce_product.as_dict()
                    if isinstance(self.woocommerce_product, WooCommerceProduct)
                    else self.woocommerce_product
                )
            except ValidationError as e:
                woocommerce_product_dict = self.woocommerce_product
            error_message = f"{frappe.get_traceback()}\n\nItem Data: \n{str(self.item) if self.item else ''}\n\nWC Product Data \n{str(woocommerce_product_dict) if self.woocommerce_product else ''})"
            raise err

    def get_corresponding_item_or_product(self):
        """
        If we have an ERPNext Item, get the corresponding WooCommerce Product
        If we have a WooCommerce Product, get the corresponding ERPNext Item
        """
        if (
            self.item and not self.woocommerce_product and self.item.item_woocommerce_server.woocommerce_id
        ):
            # Validate that this Item's WooCommerce Server has sync enabled
            wc_server = frappe.get_cached_doc(
                "WooCommerce Server", self.item.item_woocommerce_server.woocommerce_server
            )
            if not wc_server.enable_sync:
                raise SyncDisabledError(wc_server)

            wc_products = get_list_of_wc_products(item=self.item)
            if len(wc_products) == 0:
                # raise ValueError(
                # 	f"No WooCommerce Product found with ID {self.item.item_woocommerce_server.woocommerce_id} on {self.item.item_woocommerce_server.woocommerce_server}"
                # )
                frappe.log_error(
                    title="WooCommerce Product Not Found",
                    message=f"No WooCommerce Product found for ID {self.item.item_woocommerce_server.woocommerce_id} on {self.item.item_woocommerce_server.woocommerce_server}. Recreating..."
                )
                self.create_woocommerce_product(self.item)
            else:
                self.woocommerce_product = wc_products[0]

        if self.woocommerce_product and not self.item:
            self.get_erpnext_item()

    def get_erpnext_item(self):
        """
        Get erpnext item for a WooCommerce Product
        """
        if not all(
            [self.woocommerce_product.woocommerce_server, self.woocommerce_product.woocommerce_id]
        ):
            raise ValueError("Both woocommerce_server and woocommerce_id required")

        iws = frappe.qb.DocType("Item WooCommerce Server")
        itm = frappe.qb.DocType("Item")

        and_conditions = [
            iws.woocommerce_server == self.woocommerce_product.woocommerce_server,
            iws.woocommerce_id == self.woocommerce_product.woocommerce_id,
        ]

        item_codes = (
            frappe.qb.from_(iws)
            .join(itm)
            .on(iws.parent == itm.name)
            .where(Criterion.all(and_conditions))
            .select(iws.parent, iws.name)
            .limit(1)
        ).run(as_dict=True)

        found_item = frappe.get_doc("Item", item_codes[0].parent) if item_codes else None
        if found_item:
            self.item = ERPNextItemToSync(
                item=found_item,
                item_woocommerce_server_idx=next(
                    server.idx for server in found_item.woocommerce_servers if server.name == item_codes[0].name
                ),
            )

    def sync_wc_product_with_erpnext_item(self):
        """
        Syncronise Item between ERPNext and WooCommerce
        """
        frappe.log_error("255")
        if self.item and not self.woocommerce_product:
            frappe.log_error("no woo product" )
            # create missing product in WooCommerce
            self.create_woocommerce_product(self.item)
            # self.update_woocommerce_product(self.woocommerce_product, self.item)
        elif self.woocommerce_product and not self.item:
            # create missing item in ERPNext
            self.create_item(self.woocommerce_product)
        elif self.item and self.woocommerce_product:
            # both exist, check sync hash
            frappe.log_error("264")
            self.update_woocommerce_product(self.woocommerce_product, self.item)

            if (
                10==10
                # self.woocommerce_product.woocommerce_date_modified
                # != self.item.item_woocommerce_server.woocommerce_last_sync_hash
            ):
                # frappe.log_error("269")
                # if get_datetime(self.woocommerce_product.woocommerce_date_modified) > get_datetime(
                # 	self.item.item.modified
                # ):
                # 	frappe.log_error("273")
                # 	self.update_item(self.woocommerce_product, self.item)
                if get_datetime(self.woocommerce_product.woocommerce_date_modified) < get_datetime(
                    self.item.item.modified
                ):
                    frappe.log_error("276")
                    self.update_woocommerce_product(self.woocommerce_product, self.item)

    def update_item(self, woocommerce_product: WooCommerceProduct, item: ERPNextItemToSync):
        """
        Update the ERPNext Item with fields from it's corresponding WooCommerce Product
        """
        frappe.log_error("new")
        return # Added this line bcs Woo to ERP not requred. only one side
        item_dirty = False
        if item.item.item_name != woocommerce_product.woocommerce_name:
            item.item.item_name = woocommerce_product.woocommerce_name
            item_dirty = True

        fields_updated, item.item = self.set_item_fields(item=item.item)

        wc_server = frappe.get_cached_doc("WooCommerce Server", woocommerce_product.woocommerce_server)
        if wc_server.enable_image_sync:
            wc_product_images = json.loads(woocommerce_product.images)
            if len(wc_product_images) > 0:
                if item.item.image != wc_product_images[0]["src"]:
                    item.item.image = wc_product_images[0]["src"]
                    item_dirty = True

        if item_dirty or fields_updated:
            item.item.flags.created_by_sync = True
            item.item.save()

        self.set_sync_hash()

    def push_wc_product(self, product_id: int, meta=None, **fields) -> dict:
        import requests
        # WC_API_URL = "https://demo.mrkbatx.com/wp-json/wc/v3/products"
        
        # WC_CONSUMER_KEY = "ck_17fa4858255a940690410189824285a09db3ebab"
        # WC_CONSUMER_SECRET = "cs_ae71b0ceab553b3be4798a6708c410e0636073e2"
        
        servers = frappe.get_all(
            "WooCommerce Server",
            fields=["woocommerce_server_url", "api_consumer_key", "api_consumer_secret", "enable_sync"],
            filters={"enable_sync": 1},
            limit=1
        )
        # self.wcapi = API(url=server.get("woocommerce_server_url").rstrip('/'),consumer_key=server.get("api_consumer_key"),consumer_secret=server.get("api_consumer_secret"),version="wc/v3")
        if servers and len(servers) > 0:
            server = servers[0] if isinstance(servers[0], dict) else servers[0].as_dict()       
            WC_CONSUMER_KEY = server.get("api_consumer_key")
            WC_CONSUMER_SECRET = server.get("api_consumer_secret")
            # frappe.log_error("ck",WC_CONSUMER_KEY)
            # frappe.log_error("cs",WC_CONSUMER_SECRET)
            wc_base_url = server.get("woocommerce_server_url", "").rstrip("/")
            WC_API_URL = f"{wc_base_url}/wp-json/wc/v3/products"
            # frappe.log_error("WC_API_URL",WC_API_URL)
            
        
        url = f"{WC_API_URL}/{product_id}"
        # frappe.log_error("url",url)
        payload = {}

        for k, v in fields.items():
            if v is not None:
                payload[k] = v

        if meta is not None:
            if isinstance(meta, dict):
                payload["meta_data"] = [{"key": k, "value": str(v)} for k, v in meta.items()]
            elif isinstance(meta, list):
                payload["meta_data"] = meta
            else:
                frappe.log_error(f"push_wc_product: unsupported meta type {type(meta)}")

        if not payload:
            frappe.log_error("push_wc_product called with no fields or meta")
            # frappe.log_error("2")
            return {}

        try:
            resp = requests.put(
                url,
                auth=(WC_CONSUMER_KEY, WC_CONSUMER_SECRET),
                json=payload,
                timeout=30,
            )
            if resp.status_code not in (200, 201):
                a=resp.text
                frappe.log_error("Woo API error ",a)
                return {}
            return resp.json()
        except Exception as e:
            frappe.log_error(f"Woo API exception: {e}")
            return {}
        
    def create_bundle_product(self, item, product_id=None):
        """
        Create a WooCommerce Smart Bundle Product using WooCommerce REST API (/wc/v3/products)
        with type='woosb' (Woo Smart Bundle plugin).
        """

        import requests
        import json

        try:
            bundle_name = frappe.db.get_value("Product Bundle", {"new_item_code": item.item.item_code}, "name")
            if not bundle_name:
                frappe.log_error("No Product Bundle found for item", item.item.item_code)
                return {}
            bundle_doc = frappe.get_doc("Product Bundle", bundle_name)
            
            bundle_items = []
            for row in bundle_doc.items:
                bundle_items.append({
                    "item_code": row.item_code,
                    "qty": row.qty,
                    "description": row.description or ""
                })

            if not bundle_items:
                frappe.log_error("No Product Bundle Items found", item.item.item_code)
                return {}
            woosb_ids = {}
            for idx, bi in enumerate(bundle_items):
                wc_id = frappe.db.get_value(
                    "Item WooCommerce Server",
                    {"parent": bi["item_code"]},
                    "woocommerce_id"
                )
                if wc_id:
                    key = f"k{idx}"
                    woosb_ids[key] = {
                        "id": str(wc_id),
                        "sku": bi["item_code"],
                        "qty": str(int(bi["qty"] or 1)),
                        "min": "",
                        "max": ""
                    }

            if not woosb_ids:
                frappe.log_error("No WooCommerce IDs found for bundle items", item.item.item_code)
                return {}
            # Assign core fields
            raw_name = item.item.item_name
            clean_name = self.clean_product_name(raw_name)
            frappe.log_error("clean_name",clean_name )
            wc_product = frappe.get_doc({
                "doctype": "WooCommerce Product",
                "type": "woosb",
                # "woocommerce_name": item.item.item_name,
                "woocommerce_name": (bundle_doc.description or "").strip() or clean_name,
                "woocommerce_server": item.item_woocommerce_server.woocommerce_server,
                # "woocommerce_id": wc_product_id,
                "regular_price": item.item.standard_rate or 0,
                "status": "publish",
                "meta_data": json.dumps([
                    {"key": "adv_badge", "value": "combo"},
                    {"key": "woosb_ids", "value": woosb_ids}
                ])
            })
            wc_product.flags.ignore_sync = True
            wc_product.insert(ignore_permissions=True)

            self.woocommerce_product = wc_product
            wc_product.id=wc_product.woocommerce_id
            item.item_woocommerce_server.woocommerce_id = wc_product.woocommerce_id
            # item.item_woocommerce_server.woocommerce_id = woocommerce_id
            item.item.save(ignore_permissions=True)
            try:
                self.update_woocommerce_product(self.woocommerce_product, item)
            except Exception as e:
                frappe.log_error("Failed to update WooCommerce product after creation", str(e))

            # frappe.log_error("✅ Woo Bundle Synced Successfully", f"WC ID: {woocommerce_id}")
            frappe.log_error(" Woo Bundle Synced Successfully")
        except Exception as e:
            frappe.log_error("🔥 Woo Bundle Creation Failed", frappe.get_traceback())
            return {}
        

    def clean_slug(self, text, max_length=140):
        import re
        import unidecode
        ascii_text = unidecode.unidecode(text)
        slug = re.sub(r'[^a-zA-Z0-9]+', '-', ascii_text).strip('-').lower()
        return slug[:max_length]

        
    def update_woocommerce_product(
        self, wc_product: WooCommerceProduct, item: ERPNextItemToSync
    ) -> None:
        """
        Update the WooCommerce Product with fields from it's corresponding ERPNext Item
        """
        # return

        wc_server = frappe.get_all(
            "WooCommerce Server",
            fields=["name", "enable_sync"],
            limit=1
        )

        server = wc_server[0]

        if not server.enable_sync:
            return


        frappe.log_error("351")
        wc_product_dirty = False
        is_bundle = frappe.db.exists("Product Bundle", {"new_item_code": item.item.item_code})		
        # Update properties
        raw_name = item.item.item_name
        clean_name = self.clean_product_name(raw_name)
        # frappe.log_error("clean_name",clean_name )
        # if wc_product.woocommerce_name != item.item.item_name and not is_bundle:
        #     wc_product.woocommerce_name = item.item.item_name
        #     wc_product_dirty = True
        if wc_product.woocommerce_name != clean_name and not is_bundle:
            wc_product.woocommerce_name = clean_name
            wc_product_dirty = True
        # If bundle, use its description or fallback to item_name
        if is_bundle:
            bundle_name = frappe.db.get_value("Product Bundle", {"new_item_code": item.item.item_code}, "name")
            if bundle_name:
                bundle_doc = frappe.get_doc("Product Bundle", bundle_name)
                # frappe.log_error("Bundle Description", bundle_doc.description)
                wc_product.woocommerce_name = (bundle_doc.description or "").strip() or clean_name
                wc_product_dirty = True
                
        # # Ensure slug is short and cleaned
        # short_slug = self.clean_slug(wc_product.woocommerce_name)
        # wc_product.slug = short_slug
        # wc_product_dirty = True

        product_fields_changed, wc_product = self.set_product_fields(wc_product, item)
        if product_fields_changed:
            wc_product_dirty = True
        if wc_product_dirty:
            wc_product.save()
        # product_id = wc_product.id
        # frappe.log_error("clean_name2",clean_name )
        product_id = getattr(wc_product, "id", None) or wc_product.get("id") or wc_product.get("product_id")
        self.push_wc_product(product_id, sku=item.item.item_code)

        # push images
        image_urls = str(item.item.custom_woo_image_url or "").strip()

        if image_urls:
            image_list = [url.strip() for url in image_urls.split(",") if url.strip()]

            if image_list:
                main_image = {"src": image_list[0]}
                gallery_images = [{"src": url} for url in image_list[1:]]
                self.push_wc_product(
                    product_id,
                    images=[main_image] + gallery_images
                )
        
        # push description
        description_text = self.build_item_description(item.item.item_code)
        self.push_wc_product(
            product_id,
            description=description_text,
        )

        # Sync shipping class from ERPNext custom field
        shipping_class = item.item.custom_shipping_class or ""
        if shipping_class:
            try:
                self.push_wc_product(
                    product_id,
                    shipping_class=shipping_class
                )
                frappe.log_error(
                    "Shipping class synced",
                    f"Item: {item.item.item_name}, Shipping Class: {shipping_class}"
                )
            except Exception as e:
                frappe.log_error(
                    f"Failed to push shipping class for {item.item.item_name}: {e}"
                )

        
        # Push attributes
        wc_attributes = []

        for attr in item.item.custom_woo_attribuetes or []:
            options = []
            translated_name = self.translate_text(attr.name1)
            if translated_name =="Compatible":
                raw_options = attr.values or ""
                # clean_opt = opt.strip()
                frappe.log_error("raw_options",raw_options)
                if raw_options:
                    translated_opt = self.translate_text(raw_options)
                    frappe.log_error("translated_opt",translated_opt)
                    options.append(translated_opt)
            else:
                raw_options = (attr.values or "").split(",")
                for opt in raw_options:
                    clean_opt = opt.strip()
                    # frappe.log_error("clean_opt",clean_opt)
                    if clean_opt:
                        translated_opt = self.translate_text(clean_opt)
                        # frappe.log_error("translated_opt",translated_opt)
                        options.append(translated_opt)

            wc_attributes.append({
                "id": 0,
                "name": translated_name,          
                "visible": bool(attr.visible),
                "variation": False,
                "options": options                
            })

        if wc_attributes:
            self.push_wc_product(product_id, attributes=wc_attributes)


        # 🏷 Sync Branch-wise Stock dynamically
        try:
            bins = frappe.get_all(
                "Bin",
                filters={"item_code": item.item.item_code},
                fields=["warehouse", "actual_qty"]
            )
            # frappe.log_error("bins", bins)

            meta_data = {}
            branch_entries = [b for b in bins if b.actual_qty > 0]

            for index, b in enumerate(branch_entries):
                branch_name = (
                    b.warehouse
                    .lower()                        
                    .replace("warehouse", "")       
                    .replace(" - ame", "")          
                    .strip()                        
                    .replace(" ", "-") + "-branch"  
                )
                meta_data[f"branch_stock_{index}_branch"] = branch_name
                meta_data[f"branch_stock_{index}_stock_qty"] = int(b.actual_qty)

            meta_data["branch_stock"] = len(branch_entries)

            if meta_data:
                # frappe.log_error("Branch Stock Meta", meta_data)
                self.push_wc_product(product_id, meta=meta_data)
                frappe.log_error(
                    "Branch Stock Synced",
                    f"Item: {item.item.item_code}, Meta: {meta_data}"
                )
            else:
                frappe.log_error("No Branch Stock Found", item.item.item_code)

        except Exception as e:
            frappe.log_error("Branch Stock Sync Failed", str(e))\

        # 🏷 Sync Product Quantity / Stock 
        try:
            bins = frappe.get_all(
                "Bin",
                filters={"item_code": item.item.item_code},
                fields=["actual_qty"]
            )
            total_qty = sum([b.actual_qty for b in bins])

            if total_qty > 0:
                stock_status = "instock"
                backorders = "no"  
            else:
                stock_status = "onbackorder"  
                backorders = "notify"         
            self.push_wc_product(
                product_id,
                manage_stock=True,
                stock_quantity=int(total_qty),
                stock_status=stock_status,
                backorders=backorders  
            )

            frappe.log_error(
                "Stock synced",
                f"Item: {item.item.item_code}, Qty: {total_qty}, "
                f"Status: {stock_status}, Backorders: {backorders}"
            )

        except Exception as e:
            frappe.log_error("Stock sync failed", str(e))

        # 🏷 Sync Price
        try:
            price_doc = frappe.get_all(
                "Item Price",
                filters={"item_code": item.item.item_code, "price_list": "Standard Selling"},  # Adjust price list if needed
                fields=["price_list_rate"],
                limit=1
            )

            price = price_doc[0].price_list_rate if price_doc else 0.0
            self.push_wc_product(
                product_id,
                regular_price=str(price),
                _price=str(price)
            )

            frappe.log_error(
                "Price synced",
                f"Item: {item.item.item_code}, Regular & Sale Price: {price}"
            )

        except Exception as e:
            frappe.log_error("Price sync failed", str(e))

        # push is spare parts or not
        is_spare_part = False
        compatibility_entries = item.item.custom_compatibility or []

        if compatibility_entries:
            is_spare_part = True
        self.push_wc_product(
            product_id,
            meta={
                "mark_spare_part": "1" if is_spare_part else "0"
            }

        )
        
        # ✅ Build compatibility data dynamically from ERPNext child table
        meta_data, count = self.build_compatibility_data(item.item.item_code)

        if count > 0:
            meta_data["add_compactable_details"] = str(count)
            meta_data["_add_compactable_details"] = "field_68e38a56a4d82"
            self.push_wc_product(product_id, meta=meta_data)

            frappe.log_error("Compatibility Synced",
                            f"Item: {item.item.item_name}, Total Rows: {count}")
        else:
            frappe.log_error("No Compatibility Found", item.item.item_name)
        
        # # --- Push product categories ---
        categories = []

        main_cat = (item.item.category or "").strip()
        sub_cat = (item.item.sub_category or "").strip()
        if main_cat:
            parent_id = self.get_or_create_wc_category(main_cat)  
            categories.append({"id": parent_id})
            if sub_cat:
                child_id = self.get_or_create_wc_category(sub_cat, parent_id)
                categories.append({"id": child_id})
        categories = [dict(t) for t in {tuple(d.items()) for d in categories}]
        if categories:
            try:
                self.push_wc_product(product_id, categories=categories)
                frappe.log_error(
                    "Categories Synced (EN)",
                    f"Item: {item.item.item_name}, Categories: {categories}"
                )
            except Exception:
                frappe.log_error(
                    "❌ Failed to sync categories (EN)",
                    f"{item.item.item_name}\n{frappe.get_traceback()}"
                )


        
        # Push offer_category
        offer_categories = []
        for offer in item.item.custom_offer_categories or []:
            offer_name = offer.offer_name
            offer_id = self.get_or_create_wc_offer_category(offer_name)
            offer_categories.append(offer_id)
            
        if self.push_wc_product(product_id, offer_category=offer_categories):
            frappe.log_error("offer category pushed",offer_categories)
        
        

        #  Sync "Bought Together" Items
        try:
            current_item_code = item.item.item_code
            wc_ids=[]
            # wc_ids = ["85278","80479","89909"]
            # frappe.log_error("wc_ids",wc_ids)
            # self.push_wc_product(product_id, bundle_product_items=wc_ids)
            invoices = frappe.get_all(
                "Sales Invoice Item",
                filters={"item_code": current_item_code, "parenttype": "Sales Invoice"},
                fields=["parent"],
                distinct=True,
                order_by="modified desc",
                limit=1000
            )

            if invoices:
                invoice_names = [inv.parent for inv in invoices]

                # Fetch all items from those invoices (except the current one)
                items = frappe.get_all(
                    "Sales Invoice Item",
                    filters={"parent": ["in", invoice_names], "item_code": ["!=", current_item_code]},
                    fields=["item_code"]
                )

                from collections import Counter
                item_counts = Counter([i.item_code for i in items])
                top_items = [code for code, _ in item_counts.most_common(3)]

                if top_items:
                    wc_ids = []
                    for code in top_items:
                        # wc_id = frappe.db.get_value("Item", code, "woocommerce_id")
                        wc_id = frappe.db.get_value(
                            "Item WooCommerce Server",
                            {"parent": code, "woocommerce_id": ["is", "set"]},
                            "woocommerce_id"
                        )
                        if wc_id:
                            wc_ids.append(str(wc_id))

                    if wc_ids:
                        self.push_wc_product(product_id, bundle_product_items=wc_ids)
                        frappe.log_error(
                            "✅ Bought Together Synced",
                            f"Item: {current_item_code}, Bundle Product Items: {wc_ids}"
                        )

            if not invoices or not wc_ids:
                random_items = frappe.get_all(
                    "Item WooCommerce Server",
                    filters={"woocommerce_id": ["is", "set"]},
                    fields=["parent", "woocommerce_id"],
                    order_by="RAND()",
                    limit=3
                )

                if random_items:
                    wc_ids = [str(i["woocommerce_id"]) for i in random_items if i.get("woocommerce_id")]
                    self.push_wc_product(product_id, bundle_product_items=wc_ids)
                    frappe.log_error(
                        " No invoices found — pushed random Bought Together items",
                        f"Item: {current_item_code}, Random Bundle Product Items: {wc_ids}"
                    )
                else:
                    frappe.log_error(" No random items available for Bought Together fallback", current_item_code)

        except Exception as e:
            frappe.log_error("❌ Bought Together Sync Failed", str(e))
            
    import re
    def contains_arabic(self,text):
        return bool(re.search(r'[\u0600-\u06FF]', text))
    def extract_english(self,text):
        eng = re.findall(r"[A-Za-z0-9\-\/\(\)\[\]\'\"\.\,\&\+\s]+", text)
        eng_clean = " ".join(eng).strip()
        return eng_clean
    def clean_product_name(self,name):
        name = name.strip()
        english_part = self.extract_english(name)
        has_arabic = self.contains_arabic(name)
        if english_part and has_arabic:
            return english_part
        if english_part and not has_arabic:
            return english_part
        return name
     
    # description from compatability        
    def build_item_description(self, item_code):
        item = frappe.get_doc("Item", item_code)
        lines = []

        title = item.item_name or item.item_code
        lines.append(title)

        meta_data, count = self.build_compatibility_data(item_code)

        if count == 0:
            return "\n".join(lines)

        for i in range(count):
            brand = meta_data.get(f"add_compactable_details_{i}_brand", "")
            model = meta_data.get(f"add_compactable_details_{i}_model", "")
            years = meta_data.get(f"add_compactable_details_{i}_years", "")

            part_line = (
                f"Brand - {brand}&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;"
                f"Model - {model}&nbsp;&nbsp;&nbsp;- {years}"
            )

            lines.append(part_line)

        return "\n".join(lines)


    # Trnslation of arabic to english        
    def translate_text(self, arabic_text):
        if not arabic_text:
            return ""
        
        translated = frappe.db.get_value(
            "Translation",
            {"source_text": arabic_text},
            "translated_text"
        )
        return translated or arabic_text 

          
    # compatability      
    def build_compatibility_data(self, item_code):
        meta_data = {}
        compatibility_entries = []

        main_item = frappe.get_doc("Item", item_code)
        main_compat = main_item.get("custom_compatibility") or []

        is_bundle = frappe.db.exists("Product Bundle", {"new_item_code": item_code})

        if is_bundle:
            if not main_compat:
                frappe.log_error("Bundle without comp")
                bundle = frappe.get_doc("Product Bundle", {"new_item_code": item_code})

                for child in bundle.items:
                    child_item = frappe.get_doc("Item", child.item_code)
                    child_compat = child_item.get("custom_compatibility") or []

                    if child_compat:
                        compatibility_entries.extend(child_compat)
            else:
                compatibility_entries = main_compat
        else:
            compatibility_entries = main_compat

        if not compatibility_entries:
            return {}, 0
        for index, row in enumerate(compatibility_entries):
            expanded_years = ""
            if row.years:
                try:
                    expanded_years = ",".join(expand_years(row.years))
                except Exception as e:
                    frappe.log_error("Error expanding years", f"{row.years} | {e}")

            brand = self.translate_text(row.brand or "")
            model = self.translate_text(row.model or "")

            meta_data[f"add_compactable_details_{index}_brand"] = brand
            meta_data[f"add_compactable_details_{index}_model"] = model
            meta_data[f"add_compactable_details_{index}_years"] = expanded_years
            meta_data[f"add_compactable_details_{index}_variant"] = row.fuel or ""
            meta_data[f"add_compactable_details_{index}_engine_size"] = row.engine_size or ""

        return meta_data, len(compatibility_entries)





            
    def get_or_create_wc_category(self, name, parent_id=0):
        """Get category ID by name or create if not exists."""
        try:
            # 1️⃣ Try to find category by name
            existing = self.wcapi.get("products/categories", params={"search": name}).json()
            # frappe.log_error("existing",existing)
            for cat in existing:
                if cat["name"].lower() == name.lower():
                    return cat["id"]

            # 2️⃣ Not found → create new category
            new_cat = {
                "name": name,
                "parent": parent_id or 0
            }
            res = self.wcapi.post("products/categories", new_cat).json()
            return res.get("id")

        except Exception as e:
            frappe.log_error(f"❌ Error creating/fetching category {name}: {e}")
            return None
    
    def get_or_create_wc_offer_category(self, name):
        import requests, re, unicodedata

        """Fetch existing offer categories from custom taxonomy wp/v2/offer_category"""
        try:
            # Normalize input name into slug form, e.g. "New Arrivals" → "new-arrivals"
            def slugify(value):
                value = unicodedata.normalize('NFKD', value)
                value = value.encode('ascii', 'ignore').decode('utf-8')
                value = re.sub(r'[^a-zA-Z0-9]+', '-', value)
                return value.strip('-').lower()

            search_slug = slugify(name)
            servers = frappe.get_all(
                "WooCommerce Server",
                fields=["woocommerce_server_url", "api_consumer_key", "api_consumer_secret", "enable_sync"],
                filters={"enable_sync": 1},
                limit=1
            )
            if servers and len(servers) > 0:
                server = servers[0] if isinstance(servers[0], dict) else servers[0].as_dict()  
                wc_base_url = server.get("woocommerce_server_url", "").rstrip("/")
                
            # url = f"https://demo.mrkbatx.com/wp-json/wp/v2/offer_category?search={search_slug}"
            url = f"{wc_base_url}/wp-json/wp/v2/offer_category?search={search_slug}"
            # frappe.log_error("url for create category",url)
            response = requests.get(url, auth=(self.consumer_key, self.consumer_secret))

            existing = response.json()
            # frappe.log_error("existing_offer_categories", existing)

            for cat in existing:
                cat_name = cat.get("name", "").strip().lower()
                cat_slug = cat.get("slug", "").strip().lower()

                # Match either by name or slug
                if cat_name == name.lower() or cat_slug == search_slug:
                    # frappe.log_error(f"Matched offer category '{name}' to ID {cat['id']}", "")
                    return cat["id"]

            frappe.log_error(f"Offer category '{name}' not found. Manual creation needed.", "")
            return None

        except Exception as e:
            frappe.log_error(f"Error fetching offer categories: {e}")
            return None
        

    def create_woocommerce_product(self, item: ERPNextItemToSync) -> None:
        """
        Create the WooCommerce Product with fields from its corresponding ERPNext Item.
        Fully robust against missing data and ensures wc_product is always valid.
        """
        frappe.log_error("creating ")
        wc_product = None
        
        
        is_bundle = frappe.db.exists("Product Bundle", {"new_item_code": item.item.item_code})
        if is_bundle:
            frappe.log_error("its a bundle")
            self.create_bundle_product(item, getattr(item.item_woocommerce_server, "woocommerce_id", None))
            return
        # Attempt to get existing WooCommerce Product if it exists
        try:
            if item.item_woocommerce_server.woocommerce_id:
                wc_product = frappe.get_doc(
                    "WooCommerce Product",
                    {"woocommerce_id": item.item_woocommerce_server.woocommerce_id}
                )
        except Exception as e:
            frappe.log_error(f"Failed to get existing WooCommerce Product: {e}")

        # If no existing product, create a new one
        if not wc_product:
            wc_product = frappe.get_doc({"doctype": "WooCommerce Product"})
            wc_product.type = "simple"
            wc_product.status= "publish"

            # Handle variable products (with variants)
            if item.item.has_variants:
                wc_product.type = "variable"
                attributes_list = []
                for row in item.item.attributes:
                    try:
                        item_attr = frappe.get_doc("Item Attribute", row.attribute)
                        options = [val.attribute_value for val in item_attr.item_attribute_values]
                        attributes_list.append({
                            "name": row.attribute,
                            "slug": row.attribute.lower().replace(" ", "_"),
                            "visible": True,
                            "variation": True,
                            "options": options,
                        })
                    except Exception as e:
                        frappe.log_error(f"Error loading attribute {row.attribute}: {e}")
                wc_product.attributes = json.dumps(attributes_list)

        # Assign core fields
        raw_name = item.item.item_name
        clean_name = self.clean_product_name(raw_name)
        frappe.log_error("clean_name",clean_name )
        if wc_product.woocommerce_name != clean_name:
            wc_product.woocommerce_name = clean_name
        # wc_product.woocommerce_name = item.item.item_name
        wc_product.woocommerce_server = item.item_woocommerce_server.woocommerce_server
        wc_product.regular_price = get_item_price_rate(item) or "0"

        # Set additional mapped product fields
        try:
            self.set_product_fields(wc_product, item)
        except Exception as e:
            frappe.log_error(f"Failed to set product fields: {e}")

        # Insert product into database if new
        if not wc_product.name:
            wc_product.insert()

        # Save reference
        self.woocommerce_product = wc_product
        wc_product.id=wc_product.woocommerce_id

        # Update ERPNext Item with WooCommerce ID
        try:
            item.item.reload()
            item.item_woocommerce_server.woocommerce_id = wc_product.woocommerce_id
            item.item.flags.created_by_sync = True
            item.item.save()
        except Exception as e:
            frappe.log_error(f"Failed to update ERPNext item with WC ID: {e}")
        # a=item.item_woocommerce_server.woocommerce_id
        # frappe.log_error("id",a)   
        try:
            self.update_woocommerce_product(self.woocommerce_product, item)
        except Exception as e:
            frappe.log_error("Failed to update WooCommerce product after creation", str(e))
        self.set_sync_hash()


    def create_item(self, wc_product: WooCommerceProduct) -> None:
        """
        Create an ERPNext Item from the given WooCommerce Product
        """
        return # added bcs no need to sync from woo-to-erp
  
        wc_server = frappe.get_cached_doc("WooCommerce Server", wc_product.woocommerce_server)

        # Create Item
        item = frappe.new_doc("Item")

        # Handle variants' attributes
        if wc_product.type in ["variable", "variation"]:
            self.create_or_update_item_attributes(wc_product)
            wc_attributes = json.loads(wc_product.attributes)
            for wc_attribute in wc_attributes:
                row = item.append("attributes")
                row.attribute = wc_attribute["name"]
                if wc_product.type == "variation":
                    row.attribute_value = wc_attribute["option"]

        # Handle variants
        if wc_product.type == "variable":
            item.has_variants = 1

        if wc_product.type == "variation":
            # Check if parent exists
            woocommerce_product_name = generate_woocommerce_record_name_from_domain_and_id(
                wc_product.woocommerce_server, wc_product.parent_id
            )
            parent_item, parent_wc_product = run_item_sync(
                woocommerce_product_name=woocommerce_product_name
            )
            item.variant_of = parent_item.item_code

        item.item_code = (
            wc_product.sku
            if wc_server.name_by == "Product SKU" and wc_product.sku
            else str(wc_product.woocommerce_id)
        )
        item.stock_uom = wc_server.uom or _("Nos")
        item.item_group = wc_server.item_group
        item.item_name = wc_product.woocommerce_name
        row = item.append("woocommerce_servers")
        row.woocommerce_id = wc_product.woocommerce_id
        row.woocommerce_server = wc_server.name
        item.flags.ignore_mandatory = True
        item.flags.created_by_sync = True

        if wc_server.enable_image_sync:
            wc_product_images = json.loads(wc_product.images)
            if len(wc_product_images) > 0:
                item.image = wc_product_images[0]["src"]

        modified, item = self.set_item_fields(item=item)
        item.flags.created_by_sync = True

        item.insert()

        self.item = ERPNextItemToSync(
            item=item,
            item_woocommerce_server_idx=next(
                iws.idx
                for iws in item.woocommerce_servers
                if iws.woocommerce_server == wc_product.woocommerce_server
            ),
        )

        self.set_sync_hash()

    def create_or_update_item_attributes(self, wc_product: WooCommerceProduct):
        """
        Create or update an Item Attribute
        """
        if wc_product.attributes:
            wc_attributes = json.loads(wc_product.attributes)
            for wc_attribute in wc_attributes:
                if frappe.db.exists("Item Attribute", wc_attribute["name"]):
                    # Get existing Item Attribute
                    item_attribute = frappe.get_doc("Item Attribute", wc_attribute["name"])
                else:
                    # Create a Item Attribute
                    item_attribute = frappe.get_doc(
                        {"doctype": "Item Attribute", "attribute_name": wc_attribute["name"]}
                    )

                # Get list of attribute options.
                # In variable WooCommerce Products, it's a list with key "options"
                # In a WooCommerce Product variant, it's a single value with key "option"
                options = (
                    wc_attribute["options"] if wc_product.type == "variable" else [wc_attribute["option"]]
                )

                # If no attributes values exist, or attribute values exist already but are different, remove and update them
                if len(item_attribute.item_attribute_values) == 0 or (
                    len(item_attribute.item_attribute_values) > 0
                    and set(options) != set([val.attribute_value for val in item_attribute.item_attribute_values])
                ):
                    item_attribute.item_attribute_values = []
                    for option in options:
                        row = item_attribute.append("item_attribute_values")
                        row.attribute_value = option
                        row.abbr = option.replace(" ", "")

                item_attribute.flags.ignore_mandatory = True
                if not item_attribute.name:
                    item_attribute.insert()
                else:
                    item_attribute.save()

    def set_item_fields(self, item: Item) -> Tuple[bool, Item]:
        """
        If there exist any Field Mappings on `WooCommerce Server`, attempt to synchronise their values from
        WooCommerce to ERPNext
        """
        item_dirty = False
        if item and self.woocommerce_product:
            wc_server = frappe.get_cached_doc(
                "WooCommerce Server", self.woocommerce_product.woocommerce_server
            )
            if wc_server.item_field_map:
                woocommerce_product_dict = (
                    self.woocommerce_product.deserialize_attributes_of_type_dict_or_list(
                        self.woocommerce_product.to_dict()
                    )
                )
                for map in wc_server.item_field_map:
                    erpnext_item_field_name = map.erpnext_field_name.split(" | ")

                    # We expect woocommerce_field_name to be valid JSONPath
                    jsonpath_expr = parse(map.woocommerce_field_name)
                    woocommerce_product_field_matches = jsonpath_expr.find(woocommerce_product_dict)

                    setattr(item, erpnext_item_field_name[0], woocommerce_product_field_matches[0].value)
                    item_dirty = True
        return item_dirty, item

    def set_product_fields(
        self, woocommerce_product: WooCommerceProduct, item: ERPNextItemToSync
    ) -> Tuple[bool, WooCommerceProduct]:
        """
        If there exist any Field Mappings on `WooCommerce Server`, attempt to synchronise their values from
        ERPNext to WooCommerce

        Returns true if woocommerce_product was changed
        """
        wc_product_dirty = False
        if item and woocommerce_product:
            wc_server = frappe.get_cached_doc("WooCommerce Server", woocommerce_product.woocommerce_server)
            if wc_server.item_field_map:

                # Deserialize the WooCommerce Product's list and dict fields because we want to potentially perform
                # in-place updates on the whole dict using jsonpath-ng. Use the existing class method for this.
                wc_product_with_deserialised_fields = (
                    woocommerce_product.deserialize_attributes_of_type_dict_or_list(woocommerce_product)
                )

                for map in wc_server.item_field_map:
                    erpnext_item_field_name = map.erpnext_field_name.split(" | ")
                    erpnext_item_field_value = getattr(item.item, erpnext_item_field_name[0])

                    # We expect woocommerce_field_name to be valid JSONPath
                    jsonpath_expr = parse(map.woocommerce_field_name)
                    woocommerce_product_field_matches = jsonpath_expr.find(wc_product_with_deserialised_fields)

                    if len(woocommerce_product_field_matches) == 0:
                        if woocommerce_product.name:
                            # We're strict about existing WooCommerce Products, the field should exist
                            raise ValueError(
                                _("Field <code>{0}</code> not found in WooCommerce Product {1}").format(
                                    map.woocommerce_field_name, woocommerce_product.name
                                )
                            )
                        else:
                            # For new WooCommerce Products, the nested field may not exist yet, so don't stop the sync
                            continue

                    # JSONPath parsing typically returns a list, we'll only take the first value
                    woocommerce_product_field_value = woocommerce_product_field_matches[0].value

                    if erpnext_item_field_value != woocommerce_product_field_value:
                        jsonpath_expr.update(wc_product_with_deserialised_fields, erpnext_item_field_value)
                        wc_product_dirty = True

                if wc_product_dirty:
                    # Re-serialize the WooCommerce Product's list and dict fields, because we deserialized earlier
                    woocommerce_product = woocommerce_product.serialize_attributes_of_type_dict_or_list(
                        wc_product_with_deserialised_fields
                    )

        return wc_product_dirty, woocommerce_product

    def set_sync_hash(self):
        """
        Set the last sync hash value using db.set_value, as it does not call the ORM triggers
        and it does not update the modified timestamp (by using the update_modified parameter)
        """
        frappe.db.set_value(
            "Item WooCommerce Server",
            self.item.item_woocommerce_server.name,
            "woocommerce_last_sync_hash",
            self.woocommerce_product.woocommerce_date_modified,
            update_modified=False,
        )

        # If item was synchronised but the item is set not to sync, turn on the enabled flag
        # Items that are disabled for sync will still be synced if it is ordered on WooCommerce
        frappe.db.set_value(
            "Item WooCommerce Server",
            self.item.item_woocommerce_server.name,
            "enabled",
            1,
            update_modified=False,
        )


def get_list_of_wc_products(
    item: Optional[ERPNextItemToSync] = None, date_time_from: Optional[datetime] = None
) -> List[WooCommerceProduct]:
    """
    Fetches a list of WooCommerce Products within a specified date range or linked with an Item, using pagination.

    At least one of date_time_from, item parameters are required
    """
    if not any([date_time_from, item]):
        raise ValueError("At least one of date_time_from or item parameters are required")

    wc_records_per_page_limit = 100
    page_length = wc_records_per_page_limit
    new_results = True
    start = 0
    filters = []
    wc_products = []
    servers = None

    # Build filters
    if date_time_from:
        filters.append(["WooCommerce Product", "date_modified", ">", date_time_from])
    if item:
        filters.append(["WooCommerce Product", "id", "=", item.item_woocommerce_server.woocommerce_id])
        servers = [item.item_woocommerce_server.woocommerce_server]

    while new_results:
        woocommerce_product = frappe.get_doc({"doctype": "WooCommerce Product"})
        new_results = woocommerce_product.get_list(
            args={
                "filters": filters,
                "page_lenth": page_length,
                "start": start,
                "servers": servers,
                "as_doc": True,
            }
        )
        for wc_product in new_results:
            wc_products.append(wc_product)
        start += page_length
        if len(new_results) < page_length:
            new_results = []

    return wc_products


def get_item_price_rate(item: ERPNextItemToSync):
    """
    Get the Item Price if Item Price sync is enabled
    """
    # Check if the Item Price sync is enabled
    wc_server = frappe.get_cached_doc(
        "WooCommerce Server", item.item_woocommerce_server.woocommerce_server
    )
    if wc_server.enable_price_list_sync:
        item_prices = frappe.get_all(
            "Item Price",
            filters={"item_code": item.item.item_name, "price_list": wc_server.price_list},
            fields=["price_list_rate", "valid_upto"],
        )
        return next(
            (
                price.price_list_rate
                for price in item_prices
                if not price.valid_upto or price.valid_upto > now()
            ),
            None,
        )


def clear_sync_hash_and_run_item_sync(item_code: str):
    """
    Clear the last sync hash value using db.set_value, as it does not call the ORM triggers
    and it does not update the modified timestamp (by using the update_modified parameter)
    """

    iws = frappe.qb.DocType("Item WooCommerce Server")

    iwss = (
        frappe.qb.from_(iws).where(iws.enabled == 1).where(iws.parent == item_code).select(iws.name)
    ).run(as_dict=True)

    for iws in iwss:
        frappe.db.set_value(
            "Item WooCommerce Server",
            iws.name,
            "woocommerce_last_sync_hash",
            None,
            update_modified=False,
        )

    if len(iwss) > 0:
        run_item_sync(item_code=item_code, enqueue=True)


def expand_years(text: str):
    results = []

    def normalize(year: str, base=None):
        year = year.strip()
        if len(year) == 2:  # shorthand like 14, 24, etc.
            if base and len(base) == 4:
                century = base[:2]
            else:
                century = "20"
            year = century + year
        return year

    parts = re.split(r"[,\s]+", text.strip())

    for part in parts:
        if not part:
            continue

        if "-" in part:
            start, end = part.split("-")
            start = normalize(start)
            end = normalize(end, base=start)
            for y in range(int(start), int(end) + 1):
                results.append(str(y))
        else:
            results.append(normalize(part))

    # Remove duplicates, sort numerically
    results = sorted(set(results), key=int)
    return results


@frappe.whitelist()
def bulk_run_item_sync(items):
    """
    Trigger WooCommerce sync for multiple selected items.
    """
    if isinstance(items, str):
        import json
        items = json.loads(items)

    success, failed = [], []  # ✅ fixed
    total_items = len(items)
    frappe.log_error("trotal no of items in sync",total_items)
    for idx, item_code in enumerate(items, start=1):
        try:
            frappe.log_error(f"🔄 Syncing item {idx} of {total_items}: {item_code}")
            run_item_sync(item_code=item_code, enqueue=False)
            success.append(item_code)
        except Exception as e:
            frappe.log_error(f"Bulk sync failed for {item_code}", frappe.get_traceback())
            failed.append(f"{item_code}: {str(e)}")
    # for item_code in items:
    #     try:
    #         # Run sync immediately — change to enqueue=True if you want async
    #         run_item_sync(item_code=item_code, enqueue=False)
    #         success.append(item_code)
    #     except Exception as e:
    #         frappe.log_error(f"Bulk sync failed for {item_code}", frappe.get_traceback())
    #         failed.append(f"{item_code}: {str(e)}")

    message = ""
    if success:
        message += f"✅ Successfully synced: {', '.join(success)}<br>"
    if failed:
        message += f"❌ Failed to sync: {', '.join(failed)}"
    # frappe.log_error(message, "Bulk sync completed")
    # frappe.msgprint(message, indicator="green" if not failed else "red", alert=True)
    return message