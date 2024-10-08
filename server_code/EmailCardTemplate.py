from ._anvil_designer import EmailCardTemplateTemplate
from anvil import *
import anvil.server

class EmailCardTemplate(EmailCardTemplateTemplate):
  def __init__(self, **properties):
    # Set Form properties and Data Bindings.
    self.init_components(**properties)

    # Any code you write here will run before the form opens.

  def button_view_details_click(self, **event_args):
    """This method is called when the button is clicked"""
    open_form('EmailDetailView', email_id=self.item['email_id'])