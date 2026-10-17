/* Shared lists: part categories and car makes/models (same as the Sell page). */
(function () {
  'use strict';
  var RAW = 'Toyota:Corolla,Camry,Hilux,RAV4,Land Cruiser,Prado,Vitz,Yaris,Avensis,Prius,Highlander,Hiace;Nissan:X-Trail,Altima,Sunny,Patrol,Navara,Qashqai,Juke,Note,Murano;Honda:Civic,Accord,CR-V,Fit,Jazz,Pilot;Mitsubishi:Pajero,Lancer,Outlander,L200;Mazda:CX-5,Mazda3,Mazda6,BT-50;Suzuki:Swift,Vitara,Jimny,Alto;Subaru:Forester,Outback,Impreza;Isuzu:D-Max,MU-X,Trooper;Lexus:RX,NX,ES,GX,LX;Hyundai:Tucson,Santa Fe,Elantra,Sonata,i10,Accent,Creta;Kia:Sportage,Sorento,Rio,Cerato,Picanto;Mercedes-Benz:C-Class,E-Class,S-Class,GLC,GLE,ML,G-Class,Sprinter;BMW:3 Series,5 Series,X3,X5,X6;Volkswagen:Golf,Passat,Tiguan,Polo,Jetta;Audi:A3,A4,A6,Q5,Q7;Ford:Focus,Fiesta,Ranger,Explorer,Escape;Chevrolet:Cruze,Malibu,Equinox,Tahoe;Jeep:Wrangler,Grand Cherokee;Peugeot:208,308,3008,Partner;Renault:Clio,Duster,Logan;Land Rover:Defender,Discovery,Range Rover,Evoque;Volvo:XC60,XC90;Chery:Tiggo 4,Tiggo 7;Geely:Coolray,Emgrand;BYD:Atto 3,Han,Seal';
  var MAKES = [], MODELS = {};
  RAW.split(';').forEach(function (p) { var a = p.split(':'); MAKES.push(a[0]); MODELS[a[0]] = a[1].split(','); });
  window.SAZCars = { CATS: ['Fluids', 'Filters', 'Batteries', 'Brakes', 'Engine', 'Suspension', 'Electrical', 'Tires & Wheels', 'Transmission', 'Body', 'Interior', 'Exhaust', 'Lighting', 'Audio', 'Accessories'], MAKES: MAKES, MODELS: MODELS };
})();
