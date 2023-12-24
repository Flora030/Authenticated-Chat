import socketserver
import sys
from util.request import Request
import json
import bcrypt
from pymongo import MongoClient
import secrets
import hashlib
from bson import ObjectId
import os
import hashlib
import base64
import os
from threading import Lock

client=MongoClient("mongo", 27017)
database=client["database"]
userInfoCollection=database['users']
chatMessageCollection=database['chatMessages']
tokenCollection=database['token']

websocketData=[]
websocketDataLock=Lock()
class MyTCPHandler(socketserver.BaseRequestHandler):

    def createValidResponse(self, HTTPVersion, statusCode, contentType, contentLength):
        response = (
            HTTPVersion + " " + statusCode + "\r\n" +
            "Content-Type: " + contentType + "\r\n" +
            "Content-Length: " + contentLength  + "\r\n" +
            "X-Content-Type-Options: nosniff" 
            + "\r\n\r\n"
        )
        response = (
            HTTPVersion + " " + statusCode + "\r\n" +
            "Content-Type: " + contentType + "\r\n" +
            "Content-Length: " + contentLength  + "\r\n" +
            "X-Content-Type-Options: nosniff" 
            + "\r\n\r\n"
        )
        return response
    
    def createNonValidResponse(self, HTTPVersion, statusCode, contentType, contentLength):
        response = (
                HTTPVersion + " " + statusCode + "\r\n" +
                "Content-Type: " + contentType + "\r\n" +
                "Content-Length: " + contentLength  + "\r\n" +
                "X-Content-Type-Options: nosniff" 
                + "\r\n\r\n" + "The requested content does not exist"
        )
        return response 

    def authenticateForDelete(self, objectID, request):
        userFromToken=self.findUserFromToken(request)
        document=chatMessageCollection.find_one({"_id": objectID})
        if document:
            userFromChatCollection=document["username"]
        else: 
            userFromChatCollection="Guest"
        if userFromChatCollection==userFromToken:
            return True
        return False
    
    def cookieListToDictionary(self, cookie):
        if cookie:
            cookieDict={}
            cookie_pairs=cookie.split(";")
            for pair in cookie_pairs:
                key, value=pair.split("=", 1)  # Split at the first '='
                cookieDict[key.strip()]=value.strip()
            return cookieDict
        
    
    def findUserFromToken(self,request):
        username="Guest"
        cookie=request.headers.get("Cookie", [])
        cookieDictionary=self.cookieListToDictionary(cookie)
        if cookieDictionary:
            cookieToken=cookieDictionary.get("token", "")
            if cookieToken:
                hashedToken=hashlib.sha256(cookieToken.encode()).digest()
                document=tokenCollection.find_one(({"token": hashedToken}))
                if document:
                    username=document["username"]
        return username
    
    def generateXSRF(self):
        token=secrets.token_urlsafe(20)
        return token
            
    def upgradeToWebsocket(self,key):
        appendGUIDKey=key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
        appendGUIDKey=appendGUIDKey.encode()
        # Computes the SHA-1 hash
        sha1Result=hashlib.sha1(appendGUIDKey).digest()
        # base64 encoding of the SHA-1 hash
        websocketKey=base64.b64encode(sha1Result).decode()
        response= (
            "HTTP/1.1 101 Switching Protocols" + "\r\n" +
            "Upgrade: websocket" + "\r\n" +
            "Connection: Upgrade" + "\r\n" +
            "Sec-WebSocket-Accept: " + websocketKey + "\r\n\r\n"
        )
        responseEncode=response.encode()
        self.request.sendall(responseEncode)

    def parseWebsocketFrame(self,payload):
        # The payload of each frame will be a JSON string in the format:
        # {
            # 'messageType': 'chatMessage', 
            # 'message': message_submitted_by_user
        # }
        frameDataString=payload.decode('utf-8')
        # Convert the JSON string into a dictionary
        jsonDict=json.loads(frameDataString)
        return jsonDict
    
    def websocketDisconnect(self, wsFrame):
        with websocketDataLock:
            websocketData.remove(wsFrame)
        
    def WebsocketChatData(self, username, payload, websocketData):
        try:
            jsonDict=self.parseWebsocketFrame(payload)
            if jsonDict['messageType']=='chatMessage':
                # Broadcast the message to all open WebSocket connections
                payloadToClient={
                    'messageType': 'chatMessage', 
                    'username': username,
                    'message': jsonDict["message"].replace("&", "&amp;").replace("<", "&lt;").replace(">","&gt;")
                }
                chatMessageCollection.insert_one(
                {
                    "username": username,
                    "message": jsonDict["message"].replace("&", "&amp;").replace("<", "&lt;").replace(">","&gt;")
                }
                )
                payloadToClientByte=json.dumps(payloadToClient).encode()
                frame=bytearray([0x81])
                payloadLength=len(payloadToClientByte)
                if payloadLength<=125:
                    frame.append(payloadLength) # Payload length fits in one byte
                elif payloadLength<=65535:
                    frame.append(126) # Next two bytes will have the payload length
                    frame.extend(payloadLength.to_bytes(2, 'big'))
                else:
                    frame.append(127) # Next eight bytes will have the payload length
                    frame.extend(payloadLength.to_bytes(8, 'big'))
                frame.extend(payloadToClientByte)
                with websocketDataLock:
                    for websocket in websocketData:
                        try:
                            websocket.request.sendall(frame)
                        except Exception as e:
                            print(f"An error occurred while sending data: {e}")
            elif jsonDict['messageType'] in ['webRTC-offer', 'webRTC-answer', 'webRTC-candidate']:
                otherWebsocket=None
                if len(websocketData)<2:
                    return
                if websocketData[0]==self:
                # If it is, then the other websocket is the second one in the list
                    otherWebsocket=websocketData[1]
                else:
                # Otherwise, the other websocket is the first one in the list
                    otherWebsocket=websocketData[0]
                # Forward the WebRTC message to the other WebSocket connection
                payloadToClient=json.dumps(jsonDict).encode()
                frame=bytearray([0x81])  # Text frame opcode
                # Assuming payload length fits in one byte (less than 126)
                payloadLength=len(payloadToClient)
                if payloadLength<=125:
                    frame.append(payloadLength) # Payload length fits in one byte
                elif payloadLength<=65535:
                    frame.append(126) # Next two bytes will have the payload length
                    frame.extend(payloadLength.to_bytes(2, 'big'))
                else:
                    frame.append(127) # Next eight bytes will have the payload length
                    frame.extend(payloadLength.to_bytes(8, 'big'))
                frame.extend(payloadToClient)
                otherWebsocket.request.sendall(frame)
        except json.JSONDecodeError as e:
            print("Failed to parse JSON payload:", payload)
            print("Error:", e)
        except Exception as e:
            print("An unexpected error occurred while processing chat data:", e)
            
    def handle(self):
        received_data = self.request.recv(2048)
        #print(self.client_address)
        #print("--- received data ---")
        #print(received_data)
        #print("--- end of data ---\n\n")
        request = Request(received_data) 

        if request.is_valid==False:
            return
    
        mimeTypes = {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "text/javascript; charset=utf-8",
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".mp4": "video/mp4",
            ".txt": "text/plain; charset=utf-8",
        }

        filePath=request.path[1:]
        foundVisited=False
        cookies = request.headers.get('Cookie', [])
        if cookies==[]:
            request.headers["Cookie"]=[]
        for index, item in enumerate(request.headers['Cookie']):
            if "visited" in item:
                visitValue=item.split('=')[1].replace(";","")
                foundVisited=True
                indexOfCookie=index
                break
        if foundVisited==False:
                visitValue="0"
                
        HTTPVersion=request.http_version
        statusCode="200 OK"
        ##########################COOKIES########################################
        if filePath=="visit-counter":
            if foundVisited==True:
                visitValue=str(int(visitValue)+1)
                request.headers['Cookie'][indexOfCookie]="visited="+ visitValue
            else:
                visitValue="1"
                request.headers['Cookie'].append("visited=1")
            body= "visit-counter #: " + visitValue
            response = (
                HTTPVersion + " " + "200 OK" + "\r\n" +
                "Content-Type: " + "text/html; charset=utf-8" + "\r\n" +
                "Content-Length: " + str(len(body))  + "\r\n" +
                "X-Content-Type-Options: nosniff" + "\r\n" +
                "Set-Cookie:" "visited=" + visitValue + ";" "Max-Age=3600"
                + "\r\n\r\n" + body
            )
            responseEncode=response.encode()
            self.request.sendall(responseEncode)
        ##########################HOMEPAGE########################################
        elif len(filePath)==0:
            XSRF=None
            username=""
            cookie=request.headers.get("Cookie", [])
            cookieDictionary=self.cookieListToDictionary(cookie)
            if cookieDictionary:
                cookieToken=cookieDictionary.get("token", "")
                hashedToken=hashlib.sha256(cookieToken.encode()).digest()
                document=tokenCollection.find_one(({"token": hashedToken}))
                if document:
                    XSRF=document["xsrf"]
                    username=document["username"]
            else: 
                username="Guest"
            try: 
                filePath="public/index.html"
                with open(filePath, "r") as file: 
                    fileContent=file.read()
                    if XSRF:
                        fileContent=fileContent.replace("{xsrfToken}",XSRF)
                    document=userInfoCollection.find_one(({"username": username}))
                    if document:
                        signal=document.get("profilePic","")
                        if signal:
                            fileContent=fileContent.replace("{profile_picture}",document["profilePic"])
                        else: 
                            fileContent=fileContent.replace("{profile_picture}","public/image/eagle.jpg")
                    else:
                        fileContent=fileContent.replace("{profile_picture}","public/image/eagle.jpg")
                    contentType='text/html; charset=utf-8'
                    contentLength=str(len(fileContent))
                    statusCode="200 OK"
                    response=self.createValidResponse(HTTPVersion, statusCode, contentType, contentLength)
                    responseEncode=response.encode() + fileContent.encode()
                    self.request.sendall(responseEncode)
            except (FileNotFoundError, IsADirectoryError) as e: ###############################
                statusCode="404 Not Found"
                split=filePath.split('.')
                mimeType= "." + split[1]
                contentType=mimeTypes.get(mimeType,"")
                contentLength=str(len("The requested content does not exist"))
                response=self.createNonValidResponse(HTTPVersion, statusCode, contentType, contentLength)
                self.request.sendall(responseEncode)
        ##########################PUBLIC########################################
        elif request.path.startswith("/public/"):
            if "." in request.path: 
                split=filePath.split('.')
                mimeType= "." + split[1]
            else: 
                 mimeType="text/html; charset=utf-8"
            try:
                with open(filePath, "rb") as file: 
                    fileContent=file.read()
                    contentType=mimeTypes.get(mimeType,"")
                    contentLength= str(len(fileContent))
                    response=self.createValidResponse(HTTPVersion, statusCode, contentType, contentLength)
                    responseEncode=response.encode() + fileContent
                    self.request.sendall(responseEncode)
            except (FileNotFoundError, IsADirectoryError) as e:
                statusCode="404 Not Found"
                contentType="text/html; charset=utf-8"
                contentLength=str(len("The requested content does not exist"))
                response=self.createNonValidResponse(HTTPVersion, statusCode, contentType, contentLength)
                responseEncode=response.encode()
                self.request.sendall(responseEncode)
        ##########################WEBSOCKET########################################
        elif request.method=="GET" and request.path=="/websocket":
            username=self.findUserFromToken(request)
            key=request.headers.get("Sec-WebSocket-Key", "")
            self.upgradeToWebsocket(key)
            global websocketData
            with websocketDataLock:
                websocketData.append(self)
            dataBuffer=bytearray() # Used to ensure that enough data is accumulated to form a complete frame before processing.
            completeMessage=bytearray() # Used to reconstruct a message from one or more frames
            while True: # Keep the connection open
                try:
                    data=self.request.recv(1024)
                    if not data:
                        break
                    dataBuffer+=data
                    while len(dataBuffer)>=2:
                        firstByte=dataBuffer[0]
                        fin=firstByte>>7
                        opcode=firstByte&0x0f # Get last 4 bits of byte
                        if opcode==0x8:
                            self.websocketDisconnect(self)
                            closeResponse=bytearray([0x88, 0x00])
                            self.request.sendall(closeResponse)
                            self.request.close()
                            return
                        secondByte=dataBuffer[1] # Receive the byte
                        maskingKeys=[]
                        mask=secondByte>>7
                        payloadLength=secondByte&0x7f
                        headerLength=2
                        if payloadLength==126:
                            if len(dataBuffer)<4:
                                # Not enough data to process the extended payload length
                                break
                            payloadLength=int.from_bytes(dataBuffer[2:4], 'big') # Read next 2 bytes for payload length
                            headerLength=4
                        elif payloadLength==127:
                            if len(dataBuffer)<10:
                                # Not enough data to process the extended payload length
                                break
                            payloadLength=int.from_bytes(dataBuffer[2:10], 'big') #Read next 8 bytes for payload length
                            headerLength=10
                        if mask:
                            if len(dataBuffer)<headerLength+4:
                                # Wait for more data
                                break
                            maskingKeys=dataBuffer[headerLength:headerLength+4]
                            headerLength=headerLength+4
                        if len(dataBuffer)<(headerLength+payloadLength):
                            break # Wait for more data 
                        payloadData=dataBuffer[headerLength:headerLength+payloadLength]
                        unmaskedPayload=bytearray(payloadLength)
                        if mask:
                            for i in range(payloadLength):
                                unmaskedPayload[i]=payloadData[i] ^ maskingKeys[i%4]
                        else:
                            unmaskedPayload=payloadData[:payloadLength]
                        if fin:
                            completeMessage.extend(unmaskedPayload)
                            self.WebsocketChatData(username, completeMessage, websocketData)
                            completeMessage.clear()
                        else:
                            completeMessage.extend(unmaskedPayload)
                        totalLength=payloadLength+headerLength
                        dataBuffer=dataBuffer[totalLength:] # Remove the processed frame from the buffer
                except Exception as e:
                    print(f"An error occurred: {e}")
                    break
            return
        ##########################SEND########################################
        elif request.method=="POST" and request.path=="/chat-message":
            input=json.loads(request.body.decode())
            userMessage=input["message"].replace("&", "&amp;").replace("<", "&lt;").replace(">","&gt;")
            if "XSRF" in input:
                userXSRF=input["XSRF"]
            else:
                userXSRF=None
            cookieToken=self.findCookieToken(request)
            if cookieToken:
                hashedToken=hashlib.sha256(cookieToken.encode()).digest()
                document=tokenCollection.find_one(({"token": hashedToken}))
                if document!=None:
                    XSRFInDocument=document.get("xsrf")
                    if XSRFInDocument!=None:
                        if XSRFInDocument!=userXSRF:
                            self.request.sendall(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")
                            return
                        else:
                            username=document.get("username")
            else:
                username="Guest"
            chatMessageCollection.insert_one(
            {
                "username": username,
                "message": userMessage,
            }
            )
            self.request.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n")
        ##########################CHAT HISTORY###################################
        elif request.method=="GET" and request.path=="/chat-history":
            contentType="application/JSON; charset=utf-8"
            itemsInCollection=chatMessageCollection.find({})
            chatHistory=json.dumps([({"message": item["message"], "id": str(item["_id"]), "username": item["username"]}) for item in itemsInCollection])
            contentLength=str(len(chatHistory))
            response=self.createValidResponse(HTTPVersion, statusCode, contentType, contentLength)
            response=response+chatHistory
            responseEncode=response.encode()
            self.request.sendall(responseEncode) 
        ##########################REGISTER########################################
        elif request.method=="POST" and request.path=="/register":
            data=request.body.decode()
            split=data.split("&")
            username=split[0].split("=")[1]
            username=username.replace("&", "&amp;").replace("<", "&lt;").replace(">","&gt;")
            password=split[1].split("=")[1]
            bytesPassword=password.encode()
            salt=bcrypt.gensalt()
            hashedPassword=bcrypt.hashpw(bytesPassword,salt)
            userInfoCollection.insert_one(
                {
                "username": username,
                "password": hashedPassword,
                "salt": salt
                }
                )
            filePath="/"
            response = (
                "HTTP/1.1 303 See Other\r\n"
                f"Location: {filePath}\r\n"
                "Content-Length: 0\r\n\r\n"
                )
            responseEncode=response.encode()
            self.request.sendall(responseEncode)
        ##########################LOGIN########################################
        elif request.method=="POST" and request.path=="/login":
            data=request.body.decode()
            split=data.split("&")
            username=split[0].split("=")[1]
            username=username.replace("&", "&amp;").replace("<", "&lt;").replace(">","&gt;")
            password=split[1].split("=")[1]
            document=userInfoCollection.find_one({"username": username})
            if document!=None:
                hashPasswordFromCollection=document["password"]
                valid=bcrypt.checkpw(password.encode(), hashPasswordFromCollection)
            else: 
                valid=False
            if username and valid:
                token=secrets.token_urlsafe(20)
                bytesToken=token.encode('utf-8')
                salt=bcrypt.gensalt()
                hashedToken=hashlib.sha256(bytesToken).digest()
                XSRFToken=self.generateXSRF()
                tokenCollection.insert_one(
                {
                    "username": username,
                    "token": hashedToken,
                    "xsrf": XSRFToken
                }
                )
                filePath="/"
                response=(
                "HTTP/1.1 303 See Other\r\n"
                f"Location: {filePath}\r\n"
                f"Set-Cookie: token={token}; HttpOnly; Max-Age=3600\r\n"
                "Content-Length: 0\r\n\r\n"
                )
                responseEncode=response.encode()
                self.request.sendall(responseEncode)
            else:
                response=(
                "HTTP/1.1 403 Forbidden\r\n"
                "Content-Length: 13\r\n\r\n"
                "Wrong Password" #Message to look for on the client-side
                )
                responseEncode=response.encode()
                self.request.sendall(responseEncode)
        ##########################DELETE########################################
        elif request.method=="DELETE" and request.path.startswith("/chat-message/"):
            messageID=request.path.split("/chat-message/",1)[1]
            objectID=ObjectId(messageID)
            cookieToken=self.findCookieToken(request)
            valid=self.authenticateForDelete(objectID, request)
            if valid:
                chatMessageCollection.delete_one({"_id": objectID})
                response=(
                    "HTTP/1.1 200 OK\r\n"
                    "Content-Length: 0\r\n\r\n"
                )
            else:
                response=(
                    "HTTP/1.1 403 FORBIDDEN\r\n"
                    "Content-Length: 0\r\n\r\n"
                )
            responseEncode=response.encode()
            self.request.sendall(responseEncode)
        ##########################UPLOAD PROFILE PIC########################################
        elif request.method=="POST" and request.path=="/profile-pic":
            # BUFFER:
            contentLength = request.headers.get("Content-Length", 0)
            if request.body:
                chunkArray=[request.body]
                currentLength=len(request.body)
            else:
                chunkArray=[]
                currentLength=0
            completeBody=b''
            if len(request.body)==int(contentLength):
                completeBody=request.body
            elif int(contentLength)!=0 and len(request.body)!=int(contentLength):
                while currentLength<int(contentLength):
                    temp=self.request.recv(min(int(contentLength)-currentLength, 2048))
                    chunkArray.append(temp)
                    currentLength+=len(temp)
                completeBody = b''.join(chunkArray)
            
            # AUTHENTICATE USER:
            username=self.findUserFromToken(request)
            if completeBody==b'':
                with open("public/index.html", "r") as file: 
                    fileContent=file.read()
                    fileContent=fileContent.replace("{profile_picture}","public/image/eagle.jpg")
                response="HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n"
                self.request.sendall(response.encode())
                return
            if username=="Guest":
                response=(
                    "HTTP/1.1 301 Moved Permanently\r\n"
                    f'Location: {"/"}\r\n'
                    "Content-Length: 0\r\n\r\n"
                    )
                responseEncode=response.encode()
                self.request.sendall(responseEncode)
            
            # FIND BOUNDARY
            contentType=request.headers.get("Content-Type","")
            contentTypeParts=contentType.split(";")
            boundary=None
            for item in contentTypeParts:
                if "boundary=" in item:
                    boundary=item.split("boundary=")[1].strip()
                    break

            #
            body=None
            if boundary:
                boundary='--' + boundary
                parts=completeBody.split(boundary.encode())
                headersRaw=None
                for part in parts:
                    if b'Content-Disposition: form-data;' in part and b'filename=' in part:
                        headersRaw, _, body=part.partition(b'\r\n\r\n')
                    # Parse headers
                    headers={}
                    if headersRaw:
                        for headerLine in headersRaw.split(b'\r\n'):
                            if headerLine:
                                name, _, value=headerLine.partition(b': ')
                                headers[name.decode()]=value.decode()

            # CREATE FILEPATH AND WRITE TO FILE
            if body:
                contentType=headers.get("Content-Type","")
                if contentType:
                    dataType=contentType.split("/")[1]
                username=username.replace('/', '')
                dataType=dataType.replace('/', '')
                filename=os.path.basename(username + "." + dataType)
                directoryPath="public/image/"
                filePath=os.path.join(directoryPath, filename)
                with open(filePath, "wb") as file:
                    file.write(body)
                userInfoCollection.update_one(
                {"username": username},
                {"$set": {"profilePic": filePath}}
                )

                # REFRESH TO HOMEPAGE
                response=(
                    "HTTP/1.1 301 Moved Permanently\r\n"
                    f'Location: {"/"}\r\n'
                    "Content-Length: 0\r\n\r\n"
                    )
                responseEncode=response.encode()
                self.request.sendall(responseEncode)
        ########################################################################
        else:
            statusCode="404 Not Found"
            split=filePath.split('.')
            if "." in request.path: 
                split=filePath.split('.')
                mimeType="." + split[1]
                contentType=mimeTypes.get(mimeType,"")
            else: 
                contentType="text/html; charset=utf-8"
            contentLength=str(len("The requested content does not exist"))
            response=self.createNonValidResponse(HTTPVersion, statusCode, contentType, contentLength)
            responseEncode=response.encode()
            self.request.sendall(responseEncode)

def main():
    host = "0.0.0.0"
    port = 8080

    socketserver.TCPServer.allow_reuse_address = True

    server = socketserver.ThreadingTCPServer((host, port), MyTCPHandler)

    print("Listening on port " + str(port))
    sys.stdout.flush()
    sys.stderr.flush()

    server.serve_forever()


if __name__ == "__main__":
    main()
