from ._anvil_designer import TestFormTemplate
from anvil import *
import anvil.server

class TestForm(TestFormTemplate):
  def __init__(self, **properties):
    # Set Form properties and Data Bindings.
    self.init_components(**properties)
    
    # Create UI components programmatically
    self.create_ui_components()
    
    # Populate the form type dropdown
    self.drop_down_form_type.items = ['8850', '8QF', 'NYYF_1']
    
    # Initialize the email list
    self.refresh_email_list()

  def create_ui_components(self):
    # Create dropdown for form type
    self.drop_down_form_type = DropDown(items=['8850', '8QF', 'NYYF_1'])
    self.add_component(self.drop_down_form_type)

    # Create text box for file URL
    self.text_box_file_url = TextBox(placeholder="Enter file URL")
    self.add_component(self.text_box_file_url)

    # Create buttons
    self.button_process_form = Button(text="Process Form", click=self.button_process_form_click)
    self.button_view_processed_forms = Button(text="View Processed Forms", click=self.button_view_processed_forms_click)
    self.button_refresh_emails = Button(text="Refresh Emails", click=self.button_refresh_emails_click)
    self.button_create_ui = Button(text="Create UI", click=self.button_create_ui_click)
    
    for button in [self.button_process_form, self.button_view_processed_forms, 
                   self.button_refresh_emails, self.button_create_ui]:
      self.add_component(button)

    # Create repeating panel for emails
    self.repeating_panel_emails = RepeatingPanel()
    self.add_component(self.repeating_panel_emails)

    # Create text area for results
    self.text_area_results = TextArea(readonly=True)
    self.add_component(self.text_area_results)

  def button_get_structure_click(self, **event_args):
    """This method is called when the button is clicked"""
    structure = anvil.server.call('get_database_structure')
    self.text_area_results.text = "Database Structure:\n\n"
    for table, columns in structure.items():
      self.text_area_results.text += f"Table: {table}\n"
      for column in columns:
        self.text_area_results.text += f"  - {column['name']} ({column['type']}) {column['nullable']}\n"
      self.text_area_results.text += "\n"

  def button_process_form_click(self, **event_args):
    """This method is called when the Process Form button is clicked"""
    form_type = self.drop_down_form_type.selected_value
    file_url = self.text_box_file_url.text
    
    if not form_type or not file_url:
      self.text_area_results.text = "Please select a form type and enter a file URL."
      return
    
    result = anvil.server.call('process_form', form_type, file_url)
    self.text_area_results.text = f"Form Processing Result:\n\n"
    for key, value in result.items():
      self.text_area_results.text += f"{key}: {value}\n"

  def button_view_processed_forms_click(self, **event_args):
    """This method is called when the View Processed Forms button is clicked"""
    processed_forms = anvil.server.call('get_processed_forms')
    self.text_area_results.text = "Processed Forms:\n\n"
    for form in processed_forms:
      self.text_area_results.text += f"Form ID: {form['id']}, Type: {form['type']}, Status: {form['status']}\n"

  def refresh_email_list(self):
    """Refresh the email list"""
    emails = anvil.server.call('get_all_emails')
    self.repeating_panel_emails.items = emails

  def button_refresh_emails_click(self, **event_args):
    """This method is called when the Refresh Emails button is clicked"""
    anvil.server.call('refresh_email_list')
    self.refresh_email_list()

  def button_create_ui_click(self, **event_args):
    """This method is called when the Create UI button is clicked"""
    anvil.server.call('create_ui')
    alert("UI created successfully!")
