import math
import numpy as np
import scipy.integrate
import matplotlib.pyplot as plt
import matplotlib.widgets  # Cursor
import matplotlib.dates
from datetime import datetime
import shared

import world_data
import population

COUNTRY = 'Germany'  # e.g. 'all' South Korea' 'France'  'Republic of Korea' 'Italy' 'Germany'  'US' 'Spain'
PROVINCE = 'all'  # 'all' 'Hubei'  # for provinces other than Hubei the population value needs to be set manually
EXCLUDECOUNTRIES = ['China'] if COUNTRY == 'all' else []  # massive measures early on

population = population.get_population(COUNTRY, PROVINCE, EXCLUDECOUNTRIES)


# --- external parameters ---

florida_population = 21_992_985

daysTotal = 365  # total days to model
dataOffset = 'auto'  # position of real world data relative to model in whole days.
# 'auto' will choose optimal offset based on matching of deaths curves
E0 = 1  # number of exposed people at initial time step


# --- interactive parameters ---
population = florida_population
intensive_units = 5_604  # your country
date_of_first_infection = datetime.strptime('02-01-2020', '%m-%d-%Y')  # an estimate is fine
date_of_lockdown = datetime.strptime('04-01-2020', '%m-%d-%Y')

r0 = 3.0  # https://en.wikipedia.org/wiki/Basic_reproduction_number
r1 = 1.0  # reproduction number after quarantine measures - https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3539694

# --- derived parameters ---
days_before_lockdown = (date_of_lockdown - date_of_first_infection).days

#  print(f"Days before lockdown: {days_before_lockdown}")

# almost half infections take place before symptom onset (Drosten)
# https://www.medrxiv.org/content/10.1101/2020.03.08.20032946v1.full.pdf
days_presymptomatic = 2.5
days_to_incubation = 5.2

# sigma: The rate at which an exposed person becomes infective.  symptom onset - presympomatic
sigma = 1.0 / (days_to_incubation - days_presymptomatic)

# for SEIR: generation_time = 1/sigma + 0.5 * 1/gamma = timeFromInfectionToInfectiousness + timeInfectious  https://en.wikipedia.org/wiki/Serial_interval
generation_time = 4.6  # https://www.medrxiv.org/content/10.1101/2020.03.05.20031815v1  http://www.cidrap.umn.edu/news-perspective/2020/03/short-time-between-serial-covid-19-cases-may-hinder-containment

# gamma: The rate an infectious person recovers and moves into the resistant phase.
# Note that for the model it only means he does not infect anybody any more.
gamma = 1.0 / (2.0 * (generation_time - 1.0 / sigma))

percent_asymptomatic = 0.35  # https://www.zmescience.com/medicine/iceland-testing-covid-19-0523/  but virus can already be found in throat 2.5 days before symptoms (Drosten)
# wild guess! italy:16? germany:4 south korea: 4?  a lot of the mild cases will go undetected  assuming 100% correct tests
percent_cases_detected = (1.0 - percent_asymptomatic) / 20.0

timeInHospital = 12
timeInfected = 1.0 / gamma  # better timeInfectious?

# lag, whole days - need sources
presymptomaticLag = round(days_presymptomatic)  # effort probably not worth to be more precise than 1 day
communicationLag = 2
testLag = 3
symptomToHospitalLag = 5
hospitalToIcuLag = 5

infectionFatalityRateA = 0.01  # Diamond Princess, age corrected
infectionFatalityRateB = infectionFatalityRateA * 3.0  # higher lethality without ICU - by how much?  even higher without oxygen and meds
icuRate = infectionFatalityRateA * 2  # Imperial College NPI study: hospitalized/ICU/fatal = 6/2/1

beta0 = r0 * gamma  # The parameter controlling how often a susceptible-infected contact results in a new infection.
beta1 = r1 * gamma  # beta0 is used during days0 phase, beta1 after days0

s1 = 0.5 * (-(sigma + gamma) + math.sqrt((sigma + gamma) ** 2 + 4 * sigma * gamma * (
            r0 - 1)))  # https://hal.archives-ouvertes.fr/hal-00657584/document page 13
doublingTime = (math.log(2.0, math.e) / s1)


def model(Y, x, N, beta0, days0, beta1, gamma, sigma):
    # :param array x: Time step (days)
    # :param int N: Population
    # :param float beta: The parameter controlling how often a susceptible-infected contact results in a new infection.
    # :param float gamma: The rate an infected recovers and moves into the resistant phase.
    # :param float sigma: The rate at which an exposed person becomes infective.

    S, E, I, R = Y

    beta = beta0 if x < days0 else beta1

    dS = - beta * S * I / N
    dE = beta * S * I / N - sigma * E
    dI = sigma * E - gamma * I
    dR = gamma * I
    return dS, dE, dI, dR


def solve(model, population, E0, beta0, days0, beta1, gamma, sigma):
    X = np.arange(daysTotal)  # time steps array
    N0 = population - E0, E0, 0, 0  # S, E, I, R at initial step

    y_data_var = scipy.integrate.odeint(model, N0, X, args=(population, beta0, days0, beta1, gamma, sigma))

    S, E, I, R = y_data_var.T  # transpose and unpack
    return X, S, E, I, R  # note these are all arrays


X, S, E, I, R = solve(model, population, E0, beta0, days_before_lockdown, beta1, gamma, sigma)

# import pdb; pdb.set_trace()

# derived arrays
F = I * percent_cases_detected
U = I * icuRate * timeInHospital / timeInfected  # scale for short infectious time vs. real time in hospital
P = I / population * 1_000_000  # probability of random person to be infected

# timeline: exposed, infectious, symptoms, at home, hospital, ICU
F = shared.delay(F,
                 days_presymptomatic + symptomToHospitalLag + testLag + communicationLag)  # found in tests and officially announced; from I
U = shared.delay(U, days_presymptomatic + symptomToHospitalLag + hospitalToIcuLag)  # ICU  from I before delay
U = shared.delay(U, round(
    (timeInHospital / timeInfected - 1) * timeInfected))  # ??? delay by scaling? todo: think this through

# cumulate found --> cases
FC = np.cumsum(F)

# estimate deaths from recovered
D = np.arange(daysTotal)
RPrev = 0
DPrev = 0
for i, x in enumerate(X):
    IFR = infectionFatalityRateA if U[i] <= intensive_units else infectionFatalityRateB
    D[i] = DPrev + IFR * (R[i] - RPrev)
    RPrev = R[i]
    DPrev = D[i]

D = shared.delay(D,
                 - timeInfected + days_presymptomatic + symptomToHospitalLag + timeInHospital + communicationLag)  # deaths  from R

# Plot

# this boolean determines whether the plot has a log scale on y-axis
log_plot = 1

fig = plt.figure(dpi=75, figsize=(20, 16))
ax = fig.add_subplot(111)
ax.fmt_xdata = matplotlib.dates.DateFormatter('%Y-%m-%d')  # higher date precision for cursor display
if log_plot:
    ax.set_yscale("log", nonposy='clip')

# actual country data
XCDR_data = np.array(world_data.get_country_xcdr(COUNTRY, PROVINCE,
                                                 excludeCountries=EXCLUDECOUNTRIES, returnDates=True))
dataOffset = shared.get_offset_X(XCDR_data, D,
                                 dataOffset)  # match model day to real data day for deaths curve  todo: percentage wise?

ax.plot(XCDR_data[:, 0], XCDR_data[:, 1], 'o', color='orange', alpha=0.5, lw=1,
        label='cases actually detected in tests')
ax.plot(XCDR_data[:, 0], XCDR_data[:, 2], 'x', color='black', alpha=0.5, lw=1, label='actually deceased')

# set model time to real world time
X = shared.model_to_world_time(X - dataOffset, XCDR_data)

# model data
# ax.plot(X, S, 'b', alpha=0.5, lw=2, label='Susceptible')
ax.plot(X, E, 'y', alpha=0.5, lw=2, label='Exposed (realtime)')
ax.plot(X, I, 'r--', alpha=0.5, lw=1, label='Infected (realtime)')
ax.plot(X, FC, color='orange', alpha=0.5, lw=1, label='Found cumulated: "cases" (lagtime)')
ax.plot(X, U, 'r', alpha=0.5, lw=2, label='ICU (realtime)')
# ax.plot(X, R, 'g', alpha=0.5, lw=1, label='Recovered with immunity')
# ax.plot(X, P, 'c', alpha=0.5, lw=1, label='Probability of infection')
ax.plot(X, D, 'k', alpha=0.5, lw=1, label='Deaths (lagtime)')

ax.plot([min(X), max(X)], [intensive_units, intensive_units], 'b-.', alpha=0.5, lw=1, label='Number of ICU available')

ax.set_xlabel('Time /days')
ax.set_ylim(bottom=1.0)

ax.grid(linestyle=':')  # b=True, which='major', c='w', lw=2, ls='-')
if EXCLUDECOUNTRIES:
    locationString = COUNTRY + "but " + ",".join(EXCLUDECOUNTRIES) + ' %dk' % (population / 1000)
locationString = COUNTRY + " " + PROVINCE + ' %dk' % (population / 1000)
icuString = "  intensive care units: %.0f" % intensive_units + " (guess)"
legend = ax.legend(title='COVID-19 SEIR model: ' + locationString + ' (beta)\n' + icuString)
legend.get_frame().set_alpha(0.5)
for spine in ('top', 'right', 'bottom', 'left'):
    ax.spines[spine].set_visible(False)
cursor = matplotlib.widgets.Cursor(ax, color='black', linewidth=1)

# text output
print("sigma: %.3f  1/sigma: %.3f    gamma: %.3f  1/gamma: %.3f" % (sigma, 1.0 / sigma, gamma, 1.0 / gamma))
print("beta0: %.3f" % beta0, "   beta1: %.3f" % beta1)


def print_info(i):
    print("day %d" % i)
    print(" Infected: %d" % I[i], "%.1f" % (I[i] * 100.0 / population))
    print(" Infected found: %d" % F[i], "%.1f" % (F[i] * 100.0 / population))
    print(" Infected found cumulated ('cases'): %d" % FC[i], "%.1f" % (FC[i] * 100.0 / population))
    print(" Hospital: %d" % U[i], "%.1f" % (U[i] * 100.0 / population))
    print(" Recovered: %d" % R[i], "%.1f" % (R[i] * 100.0 / population))
    print(" Deaths: %d" % D[i], "%.1f" % (D[i] * 100.0 / population))


print_info(days_before_lockdown)
print_info(daysTotal - 1)
print("findratio: %.1f%%" % (percent_cases_detected * 100.0))
print("doubling0 every ~%.1f" % doublingTime, "days")
print("lockdown measures start:", X[days_before_lockdown])

if 1:
    plt.show()
else:
    plt.savefig('model_run.png')
