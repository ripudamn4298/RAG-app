import streamlit as st
from snowflake.snowpark.context import get_active_session
from snowflake.cortex import Complete
from snowflake.core import Root
import pandas as pd
import json

pd.set_option("max_colwidth", None)

### Default Values
NUM_CHUNKS = 3  # Number of chunks for context
slide_window = 7  # Chat history window size

# Service parameters
CORTEX_SEARCH_DATABASE = "FIN_DATABASE"
CORTEX_SEARCH_SCHEMA = "retreive_data"
CORTEX_SEARCH_SERVICE = "FINANCIAL_SEARCH_SERVICE"

# Columns to query in the service
COLUMNS = [
    "chunk",
    "relative_path",
    "segment",
    "metric_type"
]

session = get_active_session()
root = Root(session)

svc = root.databases[CORTEX_SEARCH_DATABASE].schemas[CORTEX_SEARCH_SCHEMA].cortex_search_services[CORTEX_SEARCH_SERVICE]

def config_options():
    st.sidebar.selectbox('Select your model:', (
        'mistral-large2',  # Putting our preferred model first
        'mixtral-8x7b',
        'snowflake-arctic',
        'llama3-70b'
    ), key="model_name")

    # Get unique segments for filtering
    segments = session.table('financial_chunks_table').select('segment').distinct().collect()
    
    segment_list = ['ALL']
    for seg in segments:
        segment_list.append(seg.SEGMENT)
    
    st.sidebar.selectbox('Select Business Segment', segment_list, key="segment_value")
    
    # Get unique metric types for additional filtering
    metrics = session.table('financial_chunks_table').select('metric_type').distinct().collect()
    
    metric_list = ['ALL']
    for metric in metrics:
        metric_list.append(metric.METRIC_TYPE)
    
    st.sidebar.selectbox('Select Financial Metric', metric_list, key="metric_value")

    st.sidebar.checkbox('Enable Chat Memory', key="use_chat_history", value=True)
    st.sidebar.checkbox('Show Debug Info', key="debug", value=False)
    st.sidebar.button("Clear Chat", key="clear_conversation", on_click=init_messages)
    
def init_messages():
    if st.session_state.clear_conversation or "messages" not in st.session_state:
        st.session_state.messages = []

def get_similar_chunks_search_service(query):
    if st.session_state.segment_value == "ALL" and st.session_state.metric_value == "ALL":
        response = svc.search(query, COLUMNS, limit=NUM_CHUNKS)
    else:
        filter_conditions = []
        if st.session_state.segment_value != "ALL":
            filter_conditions.append({"@eq": {"segment": st.session_state.segment_value}})
        if st.session_state.metric_value != "ALL":
            filter_conditions.append({"@eq": {"metric_type": st.session_state.metric_value}})
            
        if len(filter_conditions) == 1:
            filter_obj = filter_conditions[0]
        else:
            filter_obj = {"@and": filter_conditions}
            
        response = svc.search(query, COLUMNS, filter=filter_obj, limit=NUM_CHUNKS)

    if st.session_state.debug:
        st.sidebar.json(response.json())
    
    return response.json()

def get_chat_history():
    chat_history = []
    start_index = max(0, len(st.session_state.messages) - slide_window)
    for i in range(start_index, len(st.session_state.messages) - 1):
        chat_history.append(st.session_state.messages[i])
    return chat_history

def summarize_question_with_history(chat_history, question):
    prompt = f"""
        Based on the chat history and question below, generate a search query that combines both contexts.
        Focus on financial terms and metrics. Return only the query, no explanations.
        
        <chat_history>
        {chat_history}
        </chat_history>
        <question>
        {question}
        </question>
        """
    
    summary = Complete(st.session_state.model_name, prompt)
    
    if st.session_state.debug:
        st.sidebar.text("Generated Search Query:")
        st.sidebar.caption(summary)

    return summary.replace("'", "")

def create_prompt(question):
    if st.session_state.use_chat_history:
        chat_history = get_chat_history()
        if chat_history:
            question_summary = summarize_question_with_history(chat_history, question)
            prompt_context = get_similar_chunks_search_service(question_summary)
        else:
            prompt_context = get_similar_chunks_search_service(question)
    else:
        prompt_context = get_similar_chunks_search_service(question)
        chat_history = ""

    prompt = f"""
        You are a financial advisor assistant analyzing Swiggy's business performance.
        Use the CONTEXT between <context> and </context> tags to answer questions.
        Consider the CHAT HISTORY between <chat_history> and </chat_history> tags for context.
        When answering the question between <question> and </question> tags:
        - Be precise and data-driven
        - Use specific numbers and metrics when available
        - Don't mention that you're using any context or documents
        - Only answer based on the provided context
        - If you don't have enough information, say so clearly
        
        <chat_history>
        {chat_history}
        </chat_history>
        <context>
        {prompt_context}
        </context>
        <question>
        {question}
        </question>
        Answer:
        """
    
    json_data = json.loads(prompt_context)
    relative_paths = set(item['relative_path'] for item in json_data['results'])
    
    return prompt, relative_paths

def answer_question(question):
    prompt, relative_paths = create_prompt(question)
    response = Complete(st.session_state.model_name, prompt)
    return response, relative_paths

def main():
    st.title("🤖 Financial Advisor Bot - Swiggy Analysis")
    st.write("Ask questions about Swiggy's financial performance, business segments, and metrics.")
    
    docs_available = session.sql("ls @data").collect()
    if st.sidebar.checkbox("Show Available Documents"):
        st.sidebar.dataframe([doc["name"] for doc in docs_available])

    config_options()
    init_messages()
    
    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])
    
    if question := st.chat_input("What would you like to know about Swiggy's performance?"):
        st.session_state.messages.append({"role": "user", "content": question})
        
        with st.chat_message("user"):
            st.markdown(question)
            
        with st.chat_message("assistant"):
            message_placeholder = st.empty()
            
            question = question.replace("'", "")
            
            with st.spinner(f"Analyzing with {st.session_state.model_name}..."):
                response, relative_paths = answer_question(question)
                response = response.replace("'", "")
                message_placeholder.markdown(response)
                
                if relative_paths != "None":
                    with st.sidebar.expander("Source Documents"):
                        for path in relative_paths:
                            cmd = f"select GET_PRESIGNED_URL(@data, '{path}', 360) as URL_LINK from directory(@data)"
                            df_url_link = session.sql(cmd).to_pandas()
                            url_link = df_url_link._get_value(0, 'URL_LINK')
                            st.sidebar.markdown(f"📄 [{path}]({url_link})")
        
        st.session_state.messages.append({"role": "assistant", "content": response})

if __name__ == "__main__":
    main()