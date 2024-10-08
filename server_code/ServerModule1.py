import anvil.server

@anvil.server.callable
def get_database_structure():
    return anvil.server.call('get_database_structure')

@anvil.server.callable
def process_email(email_id):
    return anvil.server.call('process_email', email_id)