// Parcel Desk: a small sample application owned by this repository, used as the
// reference of the Replica tests. All data is synthetic.
var SHIPMENTS = [
  {id: "SH-1001", from: "Riyadh", to: "Jeddah", status: "in-transit", weight: "12 kg", carrier: "North Line"},
  {id: "SH-1002", from: "Cairo", to: "Alexandria", status: "delivered", weight: "3 kg", carrier: "Delta Post"},
  {id: "SH-1003", from: "Dubai", to: "Abu Dhabi", status: "delayed", weight: "40 kg", carrier: "Gulf Freight"},
  {id: "SH-1004", from: "Amman", to: "Aqaba", status: "in-transit", weight: "7 kg", carrier: "North Line"},
  {id: "SH-1005", from: "Doha", to: "Riyadh", status: "delivered", weight: "18 kg", carrier: "Delta Post"}
];
var LABELS = {"in-transit": "In transit", "delivered": "Delivered", "delayed": "Delayed"};

function renderList() {
  var status = document.getElementById("status").value;
  var query = document.getElementById("query").value.trim().toLowerCase();
  var rows = SHIPMENTS.filter(function (item) {
    var text = (item.id + " " + item.from + " " + item.to).toLowerCase();
    return (!status || item.status === status) && (!query || text.indexOf(query) !== -1);
  });
  var body = document.getElementById("rows");
  body.innerHTML = rows.map(function (item) {
    return "<tr><td><a href=\"details.html?id=" + item.id + "\">" + item.id + "</a></td><td>" +
      item.from + "</td><td>" + item.to + "</td><td><span class=\"status " + item.status + "\">" +
      LABELS[item.status] + "</span></td></tr>";
  }).join("");
  document.getElementById("empty").hidden = rows.length !== 0;
}

function renderDetails() {
  var id = new URLSearchParams(location.search).get("id") || "SH-1001";
  var item = SHIPMENTS.filter(function (entry) { return entry.id === id; })[0];
  var card = document.getElementById("card");
  if (!item) {
    document.getElementById("title").textContent = "Shipment not found";
    card.innerHTML = "";
    return;
  }
  document.getElementById("title").textContent = "Shipment " + item.id;
  card.innerHTML = [["From", item.from], ["To", item.to], ["Status", LABELS[item.status]],
    ["Weight", item.weight], ["Carrier", item.carrier]].map(function (pair) {
      return "<dt>" + pair[0] + "</dt><dd>" + pair[1] + "</dd>";
    }).join("");
}

if (document.getElementById("rows")) {
  document.getElementById("filter").addEventListener("submit", function (event) {
    event.preventDefault();
    renderList();
  });
  renderList();
} else {
  renderDetails();
}
