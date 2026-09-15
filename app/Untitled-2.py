#Num = int(input("How many book are you going to buy: "))
#if Num >= 100:
#    Mem = input("Are you a member? (yes/no): ").lower()
#    if Mem == "yes" or Mem == "y" or Mem == "1" or Mem == "true":
#        total_price = Num * 12 * 0.8
#    else:
#        total_price = Num*12*0.9
#else:
#    total_price = Num * 12
#print(total_price)

#Age = int(input("What is your age: "))
#if Age <= 18:
#    Std = input("Are you a student? (yes/no): ").lower()
#   if Std == "yes" or Std == "y" or Std == "1" or Std == "true":
#        total_price =  12 * 0.5
#    else:
#        total_price = 12*0.7
#else:
#    total_price =  12
#print(total_price)

#Age = int(input("What is your age: "))
#Std = input("Are you a student? (yes/no): ").lower()
#if Age <=18 and (Std == "yes" or Std == "y" or Std =="1" or Std == "true"):
#    price =12*0.5
#elif Age <= 18 and (Std == "no" or Std == "n" or Std =="0" or Std == "false"):
#    price = 12*0.7
#else:
#    price =12
#print(price)

Score = int(input("What is your test score:"))
while Score<0 or Score>100:
    print("Invalid score, please try again")
    Score = int(input("What is your test score:"))
print(Score)