const canvas = document.getElementById("gameCanvas");
const ctx = canvas.getContext("2d");

function resize() {
    canvas.width = window.innerWidth;
    canvas.height = window.innerHeight;
}
window.onresize = resize;
resize();

// Simple draw
ctx.fillStyle = "white";
ctx.font = "40px Arial";
ctx.fillText("Game Starts!", 50, 100);