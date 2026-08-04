import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

const activityData = [
  { time: "08:00", admitted: 4, completed: 2 },
  { time: "09:00", admitted: 7, completed: 5 },
  { time: "10:00", admitted: 6, completed: 6 },
  { time: "11:00", admitted: 9, completed: 7 },
  { time: "12:00", admitted: 8, completed: 8 },
];

export function ActivityChart() {
  return (
    <ResponsiveContainer width="100%" height="100%">
      <AreaChart data={activityData} margin={{ top: 12, right: 8, left: -24, bottom: 0 }}>
        <defs>
          <linearGradient id="admitted" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#12805c" stopOpacity={0.32} />
            <stop offset="100%" stopColor="#12805c" stopOpacity={0.02} />
          </linearGradient>
        </defs>
        <CartesianGrid stroke="#e7ece9" vertical={false} />
        <XAxis dataKey="time" axisLine={false} tickLine={false} tick={{ fill: "#68736d", fontSize: 11 }} />
        <YAxis axisLine={false} tickLine={false} allowDecimals={false} tick={{ fill: "#68736d", fontSize: 11 }} />
        <Tooltip contentStyle={{ borderRadius: 6, border: "1px solid #d9e0dc", boxShadow: "0 8px 24px rgba(22, 30, 26, .08)" }} />
        <Area type="monotone" dataKey="admitted" stroke="#12805c" strokeWidth={2} fill="url(#admitted)" />
        <Area type="monotone" dataKey="completed" stroke="#2877b8" strokeWidth={2} fill="transparent" />
      </AreaChart>
    </ResponsiveContainer>
  );
}
