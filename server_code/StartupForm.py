from ._anvil_designer import StartupFormTemplate
from anvil import *
import anvil.server

class StartupForm(StartupFormTemplate):
  def __init__(self, **properties):
    # Set Form properties and Data Bindings.
    self.init_components(**properties)

    # Any code you write here will run before the form opens.
    anvil.server.call('create_ui')