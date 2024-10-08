import anvil.server

@anvil.server.callable
def get_database_structure():
    return anvil.server.call('get_database_structure')

@anvil.server.callable
def process_email(email_id):
    return anvil.server.call('process_email', email_id)

@anvil.server.callable
def get_all_emails():
    return anvil.server.call('get_all_emails')

@anvil.server.callable
def create_ui():
    return anvil.server.call('create_ui')

@anvil.server.callable
def refresh_email_list():
    return anvil.server.call('refresh_email_list')