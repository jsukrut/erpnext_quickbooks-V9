from __future__ import unicode_literals
import frappe
from frappe import _
import requests.exceptions
from .utils import make_quickbooks_log, pagination
from pyqb.quickbooks.batch import batch_create, batch_delete
from pyqb.quickbooks.objects.customer import Customer 

def sync_customers(quickbooks_obj):
	"""Fetch Customer data from QuickBooks"""
	quickbooks_customer_list = []
	business_objects = "Customer"
	get_qb_customer = pagination(quickbooks_obj, business_objects)
	if get_qb_customer:
		sync_qb_customers(get_qb_customer,quickbooks_customer_list)
	
def sync_qb_customers(get_qb_customer, quickbooks_customer_list):
	for qb_customer in get_qb_customer:
		if not frappe.db.get_value("Customer", {"quickbooks_cust_id": qb_customer.get('Id')}, "name"):
			create_customer(qb_customer, quickbooks_customer_list)

def create_customer(qb_customer, quickbooks_customer_list):
	""" store Customer data in ERPNEXT """ 
	customer = None
	try:	
		customer = frappe.get_doc({
			"doctype": "Customer",
			"quickbooks_cust_id": str(qb_customer.get('Id')) if qb_customer.get('Id')  else str(qb_customer.get('value')),
			"customer_name" : str(qb_customer.get('DisplayName')) if qb_customer.get('DisplayName')  else str(qb_customer.get('name')),
			"customer_type" : _("Individual"),
			"customer_group" : _("Commercial"),
			"default_currency" : qb_customer['CurrencyRef'].get('value','') if qb_customer.get('CurrencyRef') else '',
			"territory" : _("All Territories"),
			"accounts" : get_party_account(qb_customer)
		})
		customer.flags.ignore_mandatory = True
		customer.insert()
		
		if customer and qb_customer.get('BillAddr'):
			create_customer_address(qb_customer, customer, qb_customer.get("BillAddr"), "Billing", 1)
		if customer and qb_customer.get('ShipAddr'):
			create_customer_address(qb_customer, customer, qb_customer.get("ShipAddr"), "Shipping", 2)
		if customer and qb_customer.get('GivenName') and qb_customer.get('PrimaryEmailAddr') and qb_customer.get('PrimaryPhone'):
			create_customer_contact(qb_customer, customer)
		
		frappe.db.commit()
		quickbooks_customer_list.append(customer.quickbooks_cust_id)

	except Exception, e:
		if e.args[0] and e.args[0].startswith("402"):
			raise e
		else:
			make_quickbooks_log(title=e.message, status="Error", method="create_customer", message=frappe.get_traceback(),
				request_data=qb_customer, exception=True)
	
	return quickbooks_customer_list

def get_party_account(qb_customer):
	quickbooks_settings = frappe.get_doc("Quickbooks Settings", "Quickbooks Settings")
	party_account = []
	party_currency = qb_customer.get('CurrencyRef').get('value') if qb_customer.get('CurrencyRef') else ''
	debtors_account = frappe.db.get_value("Account", {"account_currency": party_currency, 'account_type': 'Receivable',\
		"company": quickbooks_settings.select_company, "root_type": "Asset"}, "name")
	if party_currency and debtors_account:
		party_account.append({
			"company": quickbooks_settings.select_company,
			"account": debtors_account
			})
	return party_account

def create_customer_address(qb_customer, customer, address, type_of_address, index):
	address_title, address_type = get_address_title_and_type(customer.customer_name, type_of_address, index)
	qb_id = str(address.get("Id")) + str(address_type)
	try :
		customer_address = frappe.get_doc({
			"doctype": "Address",
			"quickbooks_address_id": qb_id,
			"address_title": address_title,
			"address_type": address_type,
			"address_line1": address.get("Line1")[:35] if address.get("Line1") else '',
			"address_line2": address.get("Line1")[35:70] if address.get("Line1") else '',
			"city": address.get("City"),
			"state": address.get("CountrySubDivisionCode"),
			"pincode": address.get("PostalCode"),
			"country": address.get("Country"),
			"email_id": qb_customer.get('PrimaryEmailAddr').get('Address') if qb_customer.get('PrimaryEmailAddr') else '',
			"phone" : qb_customer.get('Mobile').get('FreeFormNumber') if qb_customer.get('Mobile') else ''
		})
		links = customer_address.append("links", {})
		links.link_doctype = "Customer"
		links.link_name = customer.name
		customer_address.flags.ignore_mandatory = True
		customer_address.insert()
			
	except Exception, e:
		make_quickbooks_log(title=e.message, status="Error", method="create_customer_address", message=frappe.get_traceback(),
				request_data=address, exception=True)
		raise e
	
def get_address_title_and_type(customer_name, type_of_address, index):
	address_type = _(type_of_address)
	address_title = customer_name
	if frappe.db.get_value("Address", "{0}-{1}".format(customer_name.strip(), address_type)):
		address_title = "{0}-{1}".format(customer_name.strip(), index)
		
	return address_title, address_type 


"""	Sync Customer Records From ERPNext to QuickBooks """

def sync_erp_customers(quickbooks_obj):
	"""Receive Response From Quickbooks and Update quickbooks_cust_id in Customer"""
	response_from_quickbooks = sync_erp_customers_to_quickbooks(quickbooks_obj)
	if response_from_quickbooks:
		try:
			for response_obj in response_from_quickbooks.successes:
				if response_obj:
					frappe.db.sql("""UPDATE `tabCustomer` SET quickbooks_cust_id = '%s' WHERE customer_name ='%s'""" %(response_obj.Id, response_obj.DisplayName))
					frappe.db.commit()
				else:
					raise _("Does not get any response from quickbooks")	
		except Exception, e:
			make_quickbooks_log(title=e.message, status="Error", method="sync_erp_customers", message=frappe.get_traceback(),
				request_data=response_obj, exception=True)

def sync_erp_customers_to_quickbooks(quickbooks_obj):
	"""Sync ERPNext Customer to QuickBooks"""
	Customer_list = []
	for erp_cust in erp_customer_data():
		try:
			if erp_cust:
				create_erp_customer_to_quickbooks(quickbooks_obj, erp_cust, Customer_list)
			else:
				raise _("Customer does not exist in ERPNext")
		except Exception, e:
			if e.args[0] and e.args[0].startswith("402"):
				raise e
			else:
				make_quickbooks_log(title=e.message, status="Error", method="sync_erp_customers_to_quickbooks", message=frappe.get_traceback(),
					request_data=erp_cust, exception=True)
	results = batch_create(Customer_list)
	return results
	

def erp_customer_data():
	erp_customer = frappe.db.sql("""select `customer_name` from `tabCustomer` WHERE  quickbooks_cust_id IS NULL""" ,as_dict=1)
	return erp_customer

def create_erp_customer_to_quickbooks(quickbooks_obj, erp_cust, Customer_list):
	customer_obj = Customer()
	customer_obj.FullyQualifiedName = erp_cust.customer_name
	customer_obj.DisplayName = erp_cust.customer_name
	customer_obj.save()
	Customer_list.append(customer_obj)
	return Customer_list

def create_customer_contact(qb_customer, customer):
	try :
		customer_contact= frappe.get_doc({
			"doctype": "Contact",
			"first_name": qb_customer.get('GivenName'),
			"email_id": qb_customer.get('PrimaryEmailAddr').get('Address'),
			"phone": qb_customer.get('PrimaryPhone').get('FreeFormNumber')
		})
		links = customer_contact.append("links", {})
		links.link_doctype = "Customer"
		links.link_name = customer.name
		customer_contact.flags.ignore_mandatory = True
		customer_contact.insert()
			
	except Exception, e:
		make_quickbooks_log(title=e.message, status="Error", method="create_customer_address", message=frappe.get_traceback(),
				request_data=qb_customer, exception=True)
		raise e
	