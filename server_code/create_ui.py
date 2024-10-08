import anvil.server
from anvil import *
import anvil.tables as tables
import anvil.tables.query as q
from anvil.tables import app_tables

@anvil.server.callable
def create_main_form():
    form = anvil.Component()
    form.add_component(Button(text="Get Database Structure", name="button_get_structure"))
    form.add_component(Button(text="Process Email", name="button_process_email"))
    form.add_component(TextArea(name="text_area_results"))
    
    form.button_get_structure.set_event_handler('click', button_get_structure_click)
    form.button_process_email.set_event_handler('click', button_process_email_click)
    
    anvil.set_default_form(form)
    return form

def button_get_structure_click(self, **event_args):
    structure = anvil.server.call('get_database_structure')
    self.parent.text_area_results.text = "Database Structure:\n\n"
    for table, columns in structure.items():
        self.parent.text_area_results.text += f"Table: {table}\n"
        for column in columns:
            self.parent.text_area_results.text += f"  - {column['name']} ({column['type']}) {column['nullable']}\n"
        self.parent.text_area_results.text += "\n"

def button_process_email_click(self, **event_args):
    # For demonstration, we'll use a dummy email_id
    email_id = 'dummy_email_id'
    result = anvil.server.call('process_email', email_id)
    self.parent.text_area_results.text = f"Email Processing Result:\n\n"
    self.parent.text_area_results.text += f"Status: {result['status']}\n"
    if result['status'] == 'success':
        self.parent.text_area_results.text += f"Names: {result.get('names', 'N/A')}\n"
        self.parent.text_area_results.text += f"SSN: {result.get('ssn', 'N/A')}\n"
    else:
        self.parent.text_area_results.text += f"Error: {result.get('message', 'Unknown error')}\n"

# Call this function when your app starts
anvil.server.call('create_main_form')