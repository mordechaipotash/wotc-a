from ._anvil_designer import EmailDetailViewTemplate
from anvil import *
import anvil.server

class EmailDetailView(EmailDetailViewTemplate):
  def __init__(self, email_id, **properties):
    # Set Form properties and Data Bindings.
    self.init_components(**properties)

    # Any code you write here will run before the form opens.
    self.email_id = email_id
    self.load_email_details()

  def load_email_details(self):
    email_details = anvil.server.call('fetch_email_details', self.email_id)
    if email_details:
      self.text_box_subject.text = email_details['subject']
      self.text_box_from.text = email_details['from_email']
      self.text_box_to.text = email_details['to_email']
      self.text_box_date.text = str(email_details['date'])
      self.text_area_body.text = email_details['body']
    else:
      alert("Email details not found.")

  def button_back_click(self, **event_args):
    """This method is called when the button is clicked"""
    open_form('MainDashboard')