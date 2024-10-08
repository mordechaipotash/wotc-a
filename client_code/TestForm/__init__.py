from ._anvil_designer import TestFormTemplate
from anvil import *
import anvil.server

class TestForm(TestFormTemplate):
  def __init__(self, **properties):
    self.init_components(**properties)

  def button_1_click(self, **event_args):
    """This method is called when the button is clicked"""
    print("Get Database Structure button clicked")
    try:
      structure = anvil.server.call('get_database_structure')
      print(f"Received structure: {structure}")
      self.text_area_results.text = "Database Structure:\n\n"
      for table, columns in structure.items():
        self.text_area_results.text += f"Table: {table}\n"
        for column in columns:
          self.text_area_results.text += f"  - {column['name']} ({column['type']}) {column['nullable']}\n"
        self.text_area_results.text += "\n"
    except Exception as e:
      print(f"Error in button_get_structure_click: {str(e)}")
      self.text_area_results.text = f"Error retrieving database structure: {str(e)}"

  def button_process_email_click(self, **event_args):
    """This method is called when the button is clicked"""
    print("Process Email button clicked")
    try:
      # Replace 'dummy_email_id' with a text input for the email ID
      email_id = self.text_box_email_id.text
      result = anvil.server.call('process_email', email_id)
      print(f"Received result: {result}")
      if result['status'] == 'success':
        self.text_area_results.text = f"Email Processing Result:\n\n"
        self.text_area_results.text += f"Names: {result['names']}\n"
        self.text_area_results.text += f"SSN: {result['ssn']}\n"
      else:
        self.text_area_results.text = f"Error: {result['message']}"
    except Exception as e:
      print(f"Error in button_process_email_click: {str(e)}")
      self.text_area_results.text = f"Error processing email: {str(e)}"