class Request:

    def __init__(self, request: bytes):
        # Parse the bytes of the request and populate the following instance variables

        self.is_valid=True
        headerData, _, bodyData=request.partition(b'\r\n\r\n')
        try:
            # Decode only the headers part
            headers=headerData.decode('utf-8')
        except UnicodeDecodeError:
            self.is_valid=False
            return

        splitLine=headers.split('\r\n')
        requestLine=splitLine[0]
        splitRequestLine=requestLine.split()
        if len(splitRequestLine)==0:
            self.is_valid=False
            return
        
        self.method=splitRequestLine[0]
        self.path=splitRequestLine[1]
        self.http_version=splitRequestLine[2]

        self.headers={}
        for line in splitLine[1:]:
            if line:
                key, _, value=line.partition(":")
                self.headers[key.strip()]=value.strip()
        self.body=bodyData