import os.path
import os
dir_path = os.path.dirname(os.path.realpath(__file__))

import os.path
import base64
import json
import re
import time
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from bs4 import BeautifulSoup

import gradio as gr
import time


import os
import openai

def decode_base64(data):
    return base64.urlsafe_b64decode(data).decode('utf-8')

def process_parts(parts):
    contents = ""

    for part in parts:
        mime_type = part['mimeType']
        body = part.get('body', {})
        data = body.get('data', '')

        content = ""

        if "multipart" in mime_type:
          # Recursively process nested parts
            nested_parts = part.get('parts', [])
            content = process_parts(nested_parts)
        if mime_type == 'text/plain':
            try:
                data = decode_base64(data)
                soup = BeautifulSoup(data, "lxml")
                content = soup.text.strip()
            except:
                content = decode_base64(data).strip()
            content = content.replace("\n\n"," ")

        if mime_type == 'text/html':
            data = decode_base64(data)
            soup = BeautifulSoup(data, "lxml")
            content = soup.text.strip()
            content = content.replace("\n\n", " ")
        # if mime_type.startswith('image/'):
        #     content = [base64.urlsafe_b64decode(data)]
        #     content_type = ["image"]

        if len(content):
            contents = contents+content
            break

    return contents

def process_body_data(body):
    data = body.get('data',None)
    content = []
    content_type = []
    if data is not None:
        data = decode_base64(body.get('data'))
        soup = BeautifulSoup(data, "lxml")
        content = [soup.text.strip()]
        content_type = ["text"]

    return content, content_type

# If modifying these scopes, delete the file token.json.
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly",
          "https://www.googleapis.com/auth/gmail.compose",
          "https://www.googleapis.com/auth/gmail.modify"]


class Emailer():
    def __init__(self,token_file=os.path.join(dir_path,"./private/token.json"),
                 client_secret_filename="") -> object:

        try:
            os.mkdir(os.path.join(dir_path,"./private/"))
        except:
            pass

        os.environ['GMAIL_CREDENTIALS_JSON'] = client_secret_filename

        creds = None
        # The file token.json stores the user's access and refresh tokens, and is
        # created automatically when the authorization flow completes for the first
        # time.
        if os.path.exists(token_file):
            creds = Credentials.from_authorized_user_file(token_file, SCOPES)

        # If there are no (valid) credentials available, let the user log in.
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(
                    os.environ['GMAIL_CREDENTIALS_JSON'], SCOPES
                )
                creds = flow.run_local_server(port=0)
            # Save the credentials for the next run
            with open(token_file, "w") as token:
                token.write(creds.to_json())

        self.service = build("gmail", "v1", credentials=creds)

    def get_messages(self,max_results=20,labelIds=['INBOX']):
        results = self.service.users().labels().list(userId="me").execute()
        labels = results.get("labels", [])

        # q= "from:someuser@example.com rfc822msgid:<somemsgid@example.com> is:unread"
        results = self.service.users().messages().list(userId="me", maxResults=max_results, q="is:unread",
                                                  labelIds=labelIds).execute()
        messages = results.get('messages', [])

        return messages

    def parse_message(self,message):
        msg = self.service.users().messages().get(userId='me', id=message['id']).execute()
        # service.users().messages().delete(userId='me', id=message['id']).execute()

        email_data = msg['payload']
        headers = email_data['headers']
        parts = email_data.get('parts', [])
        body = email_data['body']

        topic = next(header['value'] for header in headers if header['name'] == 'Subject')
        author = next(header['value'] for header in headers if header['name'] == 'From')
        date = next(header['value'] for header in headers if header['name'] == 'Date')

        content = process_parts(parts)
        if not len(content):
            content = process_body_data(body)

        self.set_message_read(message)

        return {'author':author, 'date': date, 'topic':topic},content

    def set_message_read(self,message):
        self.service.users().messages().modify(userId='me', id=message['id'],
                                               body={'removeLabelIds': ['UNREAD']}).execute()

    def delete_message(self, message):
        self.service.users().messages().trash(userId='me', id=message['id']).execute()

class Chatter():
    def __init__(self,openai_api_key=""):
        openai.api_key = openai_api_key

        system_message = """Your are an email assistant. 
        You will be given messages from emails for you to analyze.
        The user may provide a list of authors and topics to exclude, and list of important authors and topics to consider.
        
        The user will provide you with email data containing the author, date of receipt, topic/title of the email.
        
        Your task is:
        {TASK}
        
        {INCLUDE_EXCLUDE}
        """

        retriever_task = """ You must choose whether or not to skip the email message, given the list of topics and authors of interest.
        You must provide a probability value for the likelihood of the user wanting to read the email or not.
        The value must be between 0 and 1. The higher the value, the more the topic is of interest to the user. Messages with low probability will be discarded.
        You must respond only in the following JSON dictionary format:
        {
        "probability": $prob_value, #the probability in the range (0,1)
        "reason": $reason #the reason for choosing such probability.
        }
        """
        # ```YES| GET THE FULL TEXT ```, if you don't skip the message.
        # ```NO| SKIP THE EMAIL. $REASON```, if you want to skip the email (based on the user's interests). You must provide the reason for skipping it.
        # """
        self.system_message_retriever_init = system_message.replace("{TASK}",retriever_task)

        topic_check_task = """ You must check whether or not an email sender and an email topic are of interest to the user.
        You must consider of interest also synonyms of the given topics of interest, or related topics.
        The user will provide a list of topics of interest to include. The topics and authors in the exclude list must be discareded mandatorily.
                You must respond only in the following format:
                ```yes|related_to_exclude_list``` #if at least one between the topic and the author of the email are in the provided exclude list
                ```no|not_related_to_exclude_list``` #if either the topic or author are not in the exclude list
                """

        self.system_message_topic_check_init = system_message.replace("{TASK}", topic_check_task)

        analyser_task = """You must summarize the content of the email, given the email data, the full email message.
        Your response must be in the format of a JSON dictionary as:
        ```
        {
        "summary": $summary #the main points of the email content
        }
        ```
        """
        self.system_message_analyser = system_message.replace("{TASK}", analyser_task)
        self.system_message_analyser = system_message.replace("{INCLUDE_EXCLUDE}", "")

        self.topics_include = []
        self.authors_include = []
        self.topics_exclude = []
        self.authors_exclude = []


    def set_include_exclude(self,authors_include=[], topics_include=[],
                            authors_exclude=[], topics_exclude=[]):

        self.topics_include = self.topics_include+topics_include
        self.authors_include = self.authors_include+authors_include
        self.topics_exclude = self.topics_exclude+topics_exclude
        self.authors_exclude = self.authors_exclude+authors_exclude

    def update_sys_message(self):
        message = ""
        if len(self.topics_include):
            message = message + f"""Some topics of interest to consider are {self.topics_include} \n."""

        if len(self.authors_include):
            message = message + f"""Some authors of interest to consider are: {self.authors_include}\n."""

        if len(self.topics_exclude):
            message = message + f"""You must exclude messages on the following topics and related topics: {self.topics_exclude}.\n"""

        if len(self.authors_exclude):
            message = message + f"""You must exclude messages from the following authors: {self.authors_exclude}.\n"""

        if not len(message):
            message = "There is no author or topic of specific interest."
        else:
            message = message+"""If either the author or the topic is of interest, the message must be considered."""

        self.system_message_retriever = self.system_message_retriever_init.replace("{INCLUDE_EXCLUDE}", message)
        self.system_message_topic_check = self.system_message_topic_check_init.replace("{INCLUDE_EXCLUDE}", message)

    def get_response(self,messages):
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=messages,
            temperature=0,
            max_tokens=1024,
            # n=1,
            # stop=None,
            # presence_penalty=0,
            # frequency_penalty=0.1,
        )

        text = ""
        for choice in response["choices"]:
            text = text+choice.message["content"].strip()

        return text

    def chat(self,email_data, email_message):

        user_message = f"""This is the data of the email in a dictionary format: {email_data}. """
        self.update_sys_message()

        messages_topic_check = [
            {"role": "system", "content": f"{self.system_message_topic_check}"},
            {"role": "user", "content": user_message+"\n Is the topic or author in the exclude list?"},
        ]
        response_check = self.get_response(messages_topic_check)

        response_retriever = ""
        if "no|not_in_exclude_list" in response_check.lower():
            self.authors_include.append(email_data['author'])
            self.update_sys_message()
            # response_retriever = "The author or topic are of interest. Message must not be skipped."
            response_retriever = "The author or topic are not in the initial exclude list, therefore it could be of partial interest."
            #
            # messages_retriever.append({"role": "assistant", "content": response_retriever})

        messages_retriever = [
            {"role": "system", "content": f"{self.system_message_retriever}"},
            {"role": "assistant", "content": response_retriever},
            {"role": "user", "content": user_message},
        ]
        response_retriever = self.get_response(messages_retriever)

        response_retriever = json.loads(response_retriever)
        prob = response_retriever["probability"]

        response_analyser = ""
        discard = prob < 0.5
        if not discard:

            L_max = 5000
            if len(email_message) > L_max:
                email_message = email_message[0:L_max]

            user_message = f"""This is the data of the email in a dictionary format: {email_data}.
            This is the full content of the email:
            {email_message}"""

            messages_analyser = [
                {"role": "system", "content": f"{self.system_message_analyser}"},
                {"role": "user", "content": user_message},
            ]

            response_analyser = self.get_response(messages_analyser)

        full_response = f"{email_data} \n "+str(response_retriever)+f" \n {response_analyser}"
        return discard, full_response



class myApp():

    def __init__(self):
        super().__init__()
        self.emailer = None
        self.chatter = None

    def init_emailer(self,history, filename):
        self.emailer = Emailer(client_secret_filename=filename.name)

        history = history + [(filename.name,"Emailer initialized")]

        return history

    def init_chatter(self,history, text):
        self.chatter = Chatter(text)
        history = history + [(text,"Chatter initialized")]

        return history, gr.Textbox(value="", interactive=True)

    def set_author_interests(self,history,text):
        self.chatter.set_include_exclude(authors_include=[text], topics_include=[],
                            authors_exclude=[], topics_exclude=[])

        history = history + [(text,"Set authors of interest")]

        return history,gr.Textbox(value="", interactive=True)

    def set_topics_interests(self,history,text):
        self.chatter.set_include_exclude(authors_include=[], topics_include=[text],
                            authors_exclude=[], topics_exclude=[])

        history = history + [(text,"Set topics of interest")]

        return history, gr.Textbox(value="", interactive=True)

    def set_author_exclude(self,history,text):
        self.chatter.set_include_exclude(authors_include=[text], topics_include=[],
                            authors_exclude=[text], topics_exclude=[])
        history = history + [(text,"Set authors to exclude")]

        return history,gr.Textbox(value="", interactive=True)

    def set_topics_exclude(self,history,text):
        self.chatter.set_include_exclude(authors_include=[], topics_include=[text],
                            authors_exclude=[], topics_exclude=[text])
        history = history + [(text,"Set topics to exclude")]

        return history,gr.Textbox(value="", interactive=True)

    # def add_text(self,history, text):
    #     self.human_messages.append(text)
    #     history = history + [(text, None)]
    #     return history, gr.Textbox(value="", interactive=False)

    # def add_file(self,history, file):
    #     self.uploaded_files.append(file.name)
    #     history = history + [((file.name,), None)]
    #     return history

    def bot_init(self,history):

        response = history[-1][1]
        log_text = ""
        for character in response:
            log_text += character
            history[-1][1] = log_text
            time.sleep(0.01)
            yield history

    def bot(self,history):

        messages = self.emailer.get_messages(max_results=5)
        important_messages = []
        discrarded_messages = []
        for message in messages:
            email_data, email_message = self.emailer.parse_message(message)

            discard,response = self.chatter.chat(email_data, email_message)

            if discard:
                discrarded_messages.append(response)
                self.emailer.delete_message(message)
            else:
                important_messages.append(response)

        # response = "**That's cool!**"
        response = "The Discarded Messages are: \n **"

        for message in discrarded_messages:
            response += message+"\n **"
            history = history + [(None, response)]
            time.sleep(0.01)
            yield history

        response = "The Important Messages are: \n **"
        for message in important_messages:
            response += message + "\n **"
            history = history + [(None, response)]
            time.sleep(0.01)
            yield history

if __name__ == "__main__":
    app = myApp()

    with gr.Blocks() as demo:
        gr.Markdown("Upload credential s file and OpenAI API KEY")
        chatbot = gr.Chatbot(
            [(None, "Enter openAI API KEY, credentials file, and list of topics/authors of interest")],
            elem_id="chatbot",
            bubble_full_width=False,
            # avatar_images=(None, (os.path.join(os.path.dirname(__file__), "../Bengal_123.jpg"))),
        )

        with gr.Row():
            txt_chatter = gr.Textbox(
                scale=2,
                show_label=False,
                placeholder="Enter OpenAPI Key",
                container=False,
            )
            btn_emailer = gr.UploadButton("📁", file_types=["text"])
    #
        with gr.Column():
            with gr.Row():
                txt_authors = gr.Textbox(
                scale=2,
                show_label=True,
                placeholder="Enter Authors include",
                container=False,
            )
                txt_topics = gr.Textbox(
                    scale=2,
                    show_label=True,
                    placeholder="Enter Topics include",
                    container=False,
                )
            with gr.Row():
                txt_authors_excl = gr.Textbox(
                scale=2,
                show_label=True,
                placeholder="Enter Authors exclude",
                container=False,
            )
                txt_topics_excl = gr.Textbox(
                    scale=2,
                    show_label=True,
                    placeholder="Enter Topics exclude",
                    container=False,
                )

        with gr.Row():
            btn_submit = gr.Button("Run")

        txt_msg_chatter = txt_chatter.submit(app.init_chatter, [chatbot,txt_chatter], [chatbot,txt_chatter], queue=False).then(
            app.bot_init, chatbot, chatbot, api_name="bot_chatter_init"
        )
        file_msg_emailer = btn_emailer.upload(app.init_emailer, [chatbot, btn_emailer], [chatbot], queue=False).then(
            app.bot_init, chatbot, chatbot, api_name="bot_emailer_init"
        )

        txt_msg_authors_in = txt_authors.submit(app.set_author_interests, [chatbot,txt_authors], [chatbot,txt_authors], queue=False).then(
            app.bot_init, chatbot, chatbot, api_name="bot_authors_in_init"
        )
        txt_msg_topics_in = txt_topics.submit(app.set_topics_interests, [chatbot,txt_topics], [chatbot,txt_topics], queue=False).then(
            app.bot_init, chatbot, chatbot, api_name="bot_topics_in_init"
        )
        txt_msg_authors_excl = txt_authors_excl.submit(app.set_author_exclude, [chatbot,txt_authors_excl], [chatbot,txt_authors_excl], queue=False).then(
            app.bot_init, chatbot, chatbot, api_name="bot_authors_excl_init"
        )
        txt_msg_topics_excl = txt_topics_excl.submit(app.set_topics_exclude, [chatbot,txt_topics_excl], [chatbot,txt_topics_excl], queue=False).then(
            app.bot_init, chatbot, chatbot, api_name="bot_topics_excl_init"
        )

        res = btn_submit.click(app.bot, chatbot, chatbot)
        # res.then(lambda: gr.Textbox(interactive=True), None, [txt_topics, txt_authors,txt_authors_excl,txt_topics_excl], queue=False)

    demo.queue()
    demo.launch()





