from ._anvil_designer import TestFormTemplate
from anvil import *
import anvil.server

class TestForm(TestFormTemplate):
  def __init__(self, **properties):
    self.init_components(**properties)

  def button_get_structure_click(self, **event_args):
    structure = anvil.server.call('get_database_structure')
    self.text_area_results.text = "Database Structure:\n\n"
    for table, columns in structure.items():
      self.text_area_results.text += f"Table: {table}\n"
      for column in columns:
        self.text_area_results.text += f"  - {column['name']} ({column['type']}) {column['nullable']}\n"
      self.text_area_results.text += "\n"

  def button_process_email_click(self, **event_args):
    email_id = self.text_box_email_id.text
    result = anvil.server.call('process_email', email_id)
    self.text_area_results.text = f"Email Processing Result:\n\n"
    self.text_area_results.text += f"Status: {result['status']}\n"
    if result['status'] == 'success':
      self.text_area_results.text += f"Names: {result.get('names', 'N/A')}\n"
      self.text_area_results.text += f"SSN: {result.get('ssn', 'N/A')}\n"
    else:
      self.text_area_results.text += f"Error: {result.get('message', 'Unknown error')}\n"