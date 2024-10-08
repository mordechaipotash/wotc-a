from ._anvil_designer import MainDashboardTemplate
from anvil import *
import anvil.server

class MainDashboard(MainDashboardTemplate):
  def __init__(self, **properties):
    # Set Form properties and Data Bindings.
    self.init_components(**properties)

    # Any code you write here will run before the form opens.
    self.refresh_emails()

  def refresh_emails(self):
    emails = anvil.server.call('get_all_emails')
    self.repeating_panel_1.items = emails

  def button_refresh_click(self, **event_args):
    """This method is called when the button is clicked"""
    self.refresh_emails()