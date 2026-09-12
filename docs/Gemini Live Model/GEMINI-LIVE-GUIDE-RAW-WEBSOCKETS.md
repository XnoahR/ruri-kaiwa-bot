The Gemini Live API allows for real-time, bidirectional interaction with Gemini models, supporting audio, video, and text inputs and native audio outputs. This guide explains how to integrate directly with the API using raw WebSockets.
[Try the Live API in Google AI Studio](https://aistudio.google.com/live) [Clone the example app from GitHub](https://github.com/google-gemini/gemini-live-api-examples/tree/main/gemini-live-ephemeral-tokens-websocket) [Use coding agent skills](https://ai.google.dev/gemini-api/docs/coding-agents)

## Overview

The Gemini Live API uses WebSockets for real-time communication. Unlike using an SDK, this approach involves directly managing the WebSocket connection and sending/receiving messages in a specific JSON format defined by the API.

Key concepts:

- **WebSocket Endpoint**: The specific URL to connect to.
- **Message Format** : All communication is done via JSON messages conforming to [`BidiGenerateContentClientMessage`](https://ai.google.dev/api/live#bidigeneratecontentclientmessage) and [`BidiGenerateContentServerMessage`](https://ai.google.dev/api/live#bidigeneratecontentservermessage) structures.
- **Session Management**: You are responsible for maintaining the WebSocket connection.

## Authentication

Authentication is handled by including your API key as a query parameter in the WebSocket URL.

The endpoint format is:

    wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent?key=YOUR_API_KEY

Replace `YOUR_API_KEY` with your actual API key.

## Authentication with ephemeral tokens

If you are using [ephemeral tokens](https://ai.google.dev/gemini-api/docs/ephemeral-tokens), you need to connect to the `v1beta` endpoint.
The ephemeral token needs to be passed as an `access_token` query parameter.

The endpoint format for ephemeral keys is:

    wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContentConstrained?access_token={short-lived-token}

Replace `{short-lived-token}` with the actual ephemeral token.

## Connect to the Live API

To start a live session, establish a WebSocket connection to the authenticated endpoint.
The first message sent over the WebSocket must be a [`BidiGenerateContentSetup`](https://ai.google.dev/api/live#bidigeneratecontentsetup) containing the `config`.
For the full configuration options, see the [Live API - WebSockets API reference](https://ai.google.dev/api/live).

### Python

    import asyncio
    import websockets
    import json

    API_KEY = "YOUR_API_KEY"
    MODEL_NAME = "gemini-3.1-flash-live-preview"
    WS_URL = f"wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent?key={API_KEY}"

    async def connect_and_configure():
        async with websockets.connect(WS_URL) as websocket:
            print("WebSocket Connected")

            # 1. Send the initial configuration
            setup_message = {
                "setup": {
                    "model": f"models/{MODEL_NAME}",
                    "responseModalities": ["AUDIO"],
                    "systemInstruction": {
                        "parts": [{"text": "You are a helpful assistant."}]
                    }
                }
            }
            await websocket.send(json.dumps(setup_message))
            print("Configuration sent")

            # Keep the session alive for further interactions
            await asyncio.sleep(3600) # Example: keep open for an hour

    async def main():
        await connect_and_configure()

    if __name__ == "__main__":
        asyncio.run(main())

### JavaScript

    const API_KEY = "YOUR_API_KEY";
    const MODEL_NAME = "gemini-3.1-flash-live-preview";
    const WS_URL = `wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent?key=${API_KEY}`;

    const websocket = new WebSocket(WS_URL);

    websocket.onopen = () => {
      console.log('WebSocket Connected');

      // 1. Send the initial configuration
      const setupMessage = {
        setup: {
          model: `models/${MODEL_NAME}`,
          responseModalities: ['AUDIO'],
          systemInstruction: {
            parts: [{ text: 'You are a helpful assistant.' }]
          }
        }
      };
      websocket.send(JSON.stringify(setupMessage));
      console.log('Configuration sent');
    };

    websocket.onmessage = (event) => {
      const response = JSON.parse(event.data);
      console.log('Received:', response);
      // Handle different types of responses here
    };

    websocket.onerror = (error) => {
      console.error('WebSocket Error:', error);
    };

    websocket.onclose = () => {
      console.log('WebSocket Closed');
    };

## Send text

To send text input, construct a [`BidiGenerateContentRealtimeInput`](https://ai.google.dev/api/live#bidigeneratecontentrealtimeinput) message with the `text` field.

### Python

    # Inside the websocket context
    async def send_text(websocket, text):
        text_message = {
            "realtimeInput": {
                "text": text
            }
        }
        await websocket.send(json.dumps(text_message))
        print(f"Sent text: {text}")

    # Example usage: await send_text(websocket, "Hello, how are you?")

### JavaScript

    function sendTextMessage(text) {
      if (websocket.readyState === WebSocket.OPEN) {
        const textMessage = {
          realtimeInput: {
            text: text
          }
        };
        websocket.send(JSON.stringify(textMessage));
        console.log('Text message sent:', text);
      } else {
        console.warn('WebSocket not open.');
      }
    }

    // Example usage:
    sendTextMessage("Hello, how are you?");

## Send audio

Audio needs to be sent as raw PCM data (raw 16-bit PCM audio, 16kHz, little-endian). Construct a [`BidiGenerateContentRealtimeInput`](https://ai.google.dev/api/live#bidigeneratecontentrealtimeinput) message with the audio data. The `mimeType` is crucial.

### Python

    # Inside the websocket context
    async def send_audio_chunk(websocket, chunk_bytes):
        import base64
        encoded_data = base64.b64encode(chunk_bytes).decode('utf-8')
        audio_message = {
            "realtimeInput": {
                "audio": {
                    "data": encoded_data,
                    "mimeType": "audio/pcm;rate=16000"
                }
            }
        }
        await websocket.send(json.dumps(audio_message))
        # print("Sent audio chunk") # Avoid excessive logging

    # Assuming 'chunk' is your raw PCM audio bytes
    # await send_audio_chunk(websocket, chunk)

### JavaScript

    // Assuming 'chunk' is a Buffer of raw PCM audio
    function sendAudioChunk(chunk) {
      if (websocket.readyState === WebSocket.OPEN) {
        const audioMessage = {
          realtimeInput: {
            audio: {
              data: chunk.toString('base64'),
              mimeType: 'audio/pcm;rate=16000'
            }
          }
        };
        websocket.send(JSON.stringify(audioMessage));
        // console.log('Sent audio chunk');
      }
    }
    // Example usage: sendAudioChunk(audioBuffer);

For an example of how to get the audio from the client device (e.g. the browser)
see the end-to-end example on [GitHub](https://github.com/google-gemini/gemini-live-api-examples/blob/main/gemini-live-ephemeral-tokens-websocket/frontend/mediaUtils.js#L38-L74).

## Send video

Video frames are sent as individual images (e.g., JPEG or PNG). Similar to audio, use `realtimeInput` with a `Blob`, specifying the correct `mimeType`.

### Python

    # Inside the websocket context
    async def send_video_frame(websocket, frame_bytes, mime_type="image/jpeg"):
        import base64
        encoded_data = base64.b64encode(frame_bytes).decode('utf-8')
        video_message = {
            "realtimeInput": {
                "video": {
                    "data": encoded_data,
                    "mimeType": mime_type
                }
            }
        }
        await websocket.send(json.dumps(video_message))
        # print("Sent video frame")

    # Assuming 'frame' is your JPEG-encoded image bytes
    # await send_video_frame(websocket, frame)

### JavaScript

    // Assuming 'frame' is a Buffer of JPEG-encoded image data
    function sendVideoFrame(frame, mimeType = 'image/jpeg') {
      if (websocket.readyState === WebSocket.OPEN) {
        const videoMessage = {
          realtimeInput: {
            video: {
              data: frame.toString('base64'),
              mimeType: mimeType
            }
          }
        };
        websocket.send(JSON.stringify(videoMessage));
        // console.log('Sent video frame');
      }
    }
    // Example usage: sendVideoFrame(jpegBuffer);

For an example of how to get the video from the client device (e.g. the browser)
see the end-to-end example on [GitHub](https://github.com/google-gemini/gemini-live-api-examples/blob/main/gemini-live-ephemeral-tokens-websocket/frontend/mediaUtils.js#L185-L222).

## Receive responses

The WebSocket will send back [`BidiGenerateContentServerMessage`](https://ai.google.dev/api/live#bidigeneratecontentservermessage) messages. You need to parse these JSON messages and handle different types of content.

### Python

    # Inside the websocket context, in a receive loop
    async def receive_loop(websocket):
        async for message in websocket:
            response = json.loads(message)
            print("Received:", response)

            if "serverContent" in response:
                server_content = response["serverContent"]
                # Receiving Audio
                if "modelTurn" in server_content and "parts" in server_content["modelTurn"]:
                    for part in server_content["modelTurn"]["parts"]:
                        if "inlineData" in part:
                            audio_data_b64 = part["inlineData"]["data"]
                            # Process or play the base64 encoded audio data
                            # audio_data = base64.b64decode(audio_data_b64)
                            print(f"Received audio data (base64 len: {len(audio_data_b64)})")

                # Receiving Text Transcriptions
                if "inputTranscription" in server_content:
                    print(f"User: {server_content['inputTranscription']['text']}")
                if "outputTranscription" in server_content:
                    print(f"Gemini: {server_content['outputTranscription']['text']}")

            # Handling Tool Calls
            if "toolCall" in response:
                await handle_tool_call(websocket, response["toolCall"])

    # Example usage: await receive_loop(websocket)

### JavaScript

    websocket.onmessage = (event) => {
      const response = JSON.parse(event.data);
      console.log('Received:', response);

      if (response.serverContent) {
        const serverContent = response.serverContent;
        // Receiving Audio
        if (serverContent.modelTurn?.parts) {
          for (const part of serverContent.modelTurn.parts) {
            if (part.inlineData) {
              const audioData = part.inlineData.data; // Base64 encoded string
              // Process or play audioData
              console.log(`Received audio data (base64 len: ${audioData.length})`);
            }
          }
        }

        // Receiving Text Transcriptions
        if (serverContent.inputTranscription) {
          console.log('User:', serverContent.inputTranscription.text);
        }
        if (serverContent.outputTranscription) {
          console.log('Gemini:', serverContent.outputTranscription.text);
        }
      }

      // Handling Tool Calls
      if (response.toolCall) {
        handleToolCall(response.toolCall);
      }
    };

For an example of how to handle the response, see the end-to-end example on [GitHub](https://github.com/google-gemini/gemini-live-api-examples/blob/main/gemini-live-ephemeral-tokens-websocket/frontend/geminilive.js#L22-L75).

## Handle tool calls

When the model requests a tool call, the [`BidiGenerateContentServerMessage`](https://ai.google.dev/api/live#bidigeneratecontentservermessage) will contain a `toolCall` field. You must execute the function locally and send the result back to the WebSocket using a [`BidiGenerateContentToolResponse`](https://ai.google.dev/api/live#bidigeneratecontenttoolresponse) message.

### Python

    # Placeholder for your tool function
    def my_tool_function(args):
        print(f"Executing tool with args: {args}")
        # Implement your tool logic here
        return {"status": "success", "data": "some result"}

    async def handle_tool_call(websocket, tool_call):
        function_responses = []
        for fc in tool_call["functionCalls"]:
            # 1. Execute the function locally
            try:
                result = my_tool_function(fc.get("args", {}))
                response_data = {"result": result}
            except Exception as e:
                print(f"Error executing tool {fc['name']}: {e}")
                response_data = {"error": str(e)}

            # 2. Prepare the response
            function_responses.append({
                "name": fc["name"],
                "id": fc["id"],
                "response": response_data
            })

        # 3. Send the tool response back to the session
        tool_response_message = {
            "toolResponse": {
                "functionResponses": function_responses
            }
        }
        await websocket.send(json.dumps(tool_response_message))
        print("Sent tool response")

    # This function is called within the receive_loop when a toolCall is detected.

### JavaScript

    // Placeholder for your tool function
    function myToolFunction(args) {
      console.log(`Executing tool with args:`, args);
      // Implement your tool logic here
      return { status: 'success', data: 'some result' };
    }

    function handleToolCall(toolCall) {
      const functionResponses = [];
      for (const fc of toolCall.functionCalls) {
        // 1. Execute the function locally
        let result;
        try {
          result = myToolFunction(fc.args || {});
        } catch (e) {
          console.error(`Error executing tool ${fc.name}:`, e);
          result = { error: e.message };
        }

        // 2. Prepare the response
        functionResponses.push({
          name: fc.name,
          id: fc.id,
          response: { result }
        });
      }

      // 3. Send the tool response back to the session
      if (websocket.readyState === WebSocket.OPEN) {
        const toolResponseMessage = {
          toolResponse: {
            functionResponses: functionResponses
          }
        };
        websocket.send(JSON.stringify(toolResponseMessage));
        console.log('Sent tool response');
      } else {
        console.warn('WebSocket not open to send tool response.');
      }
    }
    // This function is called within websocket.onmessage when a toolCall is detected.

## What's next

- Read the full Live API [Capabilities](https://ai.google.dev/gemini-api/docs/live-guide) guide for key capabilities and configurations; including Voice Activity Detection and native audio features.
- Read the [Tool use](https://ai.google.dev/gemini-api/docs/live-tools) guide to learn how to integrate Live API with tools and function calling.
- Read the [Session management](https://ai.google.dev/gemini-api/docs/live-session) guide for managing long running conversations.
- Read the [Ephemeral tokens](https://ai.google.dev/gemini-api/docs/ephemeral-tokens) guide for secure authentication in [client-to-server](https://ai.google.dev/gemini-api/docs/live-api/get-started-websocket#implementation-approach) applications.
- For more information about the underlying WebSockets API, see the [WebSockets API reference](https://ai.google.dev/api/live).
