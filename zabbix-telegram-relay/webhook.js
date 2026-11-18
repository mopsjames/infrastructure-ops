// Zabbix webhook media type script: forwards the media type's parameters to the relay as JSON.
var params = JSON.parse(value);
var request = new HttpRequest();
request.addHeader('Content-Type: application/json');
var response = request.post(params.url, value);
if (request.getStatus() !== 200) {
    throw 'Relay answered ' + request.getStatus() + ': ' + response;
}
return 'OK';
